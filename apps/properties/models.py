import uuid
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.core.exceptions import ValidationError
from django.db import models, transaction
from apps.markets.services import default_area_unit, default_country, default_currency  # ADR-019
from django.db.models.functions import Upper
from django.conf import settings
from django.utils import timezone

from .text_search import search_vector


# Default map — override with CITY_CODE_MAP in Django settings for other markets.
_DEFAULT_CITY_CODE_MAP = {
    'lahore': 'LHR', 'karachi': 'KHI', 'islamabad': 'ISB',
    'rawalpindi': 'RWP', 'faisalabad': 'FSD', 'multan': 'MUL',
    'peshawar': 'PEW', 'quetta': 'QTA', 'gujranwala': 'GUJ',
    'sialkot': 'SKT', 'hyderabad': 'HYD', 'abbottabad': 'ABB',
    'bahawalpur': 'BWP', 'sargodha': 'SGD', 'sukkur': 'SUK',
    'larkana': 'LRK', 'mardan': 'MRD', 'sheikhupura': 'SHP',
    'rahim yar khan': 'RYK', 'gujrat': 'GRT', 'sahiwal': 'SWL',
    'dera ghazi khan': 'DGK', 'wah cantt': 'WAH', 'chiniot': 'CHN',
}


def _get_city_code_map() -> dict:
    return getattr(settings, 'CITY_CODE_MAP', _DEFAULT_CITY_CODE_MAP)


class PropertyRefCounter(models.Model):
    city_code = models.CharField(max_length=10)
    year      = models.PositiveSmallIntegerField()
    last_seq  = models.PositiveIntegerField(default=0)

    class Meta:
        app_label       = 'properties'
        db_table        = 'property_ref_counters'
        unique_together = ('city_code', 'year')


class PropertyQuerySet(models.QuerySet):

    def visible_to(self, viewer_org=None):
        """
        Listings a consumer-facing surface (search, AI tools, recommendations,
        client API) may show. See ADR-002.

        Active listings that are live (``is_sandbox=False``) are always visible.
        Sandbox listings are visible only to their own organization's context, so
        an org can test its bot on its own inventory before go-live.
        """
        live = models.Q(is_sandbox=False)
        viewer_org_id = getattr(viewer_org, 'pk', viewer_org)
        if viewer_org_id:
            live |= models.Q(organization_id=viewer_org_id)
        return self.filter(live, is_active=True)


def _property_image_path(instance, filename):
    ext = filename.rsplit('.', 1)[-1].lower()
    return f"property_images/{instance.property_id}/{uuid.uuid4().hex}.{ext}"


class Property(models.Model):

    class PropertyType(models.TextChoices):
        RESIDENTIAL = 'residential', 'Residential'
        COMMERCIAL  = 'commercial',  'Commercial'
        PLOT        = 'plot',        'Plot'
        INDUSTRIAL  = 'industrial',  'Industrial'

    class LegalStatus(models.TextChoices):
        UNVERIFIED = 'unverified', 'Unverified'
        VERIFIED   = 'verified',   'Verified'
        DISPUTED   = 'disputed',   'Disputed'
        PENDING    = 'pending',    'Pending Verification'

    class RiskLevel(models.TextChoices):
        LOW    = 'low',    'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH   = 'high',   'High'

    class FurnishedStatus(models.TextChoices):
        FURNISHED       = 'furnished',       'Furnished'
        UNFURNISHED     = 'unfurnished',     'Unfurnished'
        SEMI_FURNISHED  = 'semi_furnished',  'Semi-Furnished'

    class ConstructionStatus(models.TextChoices):
        BUILDER_NEW        = 'builder',             'Builder / New'
        READY              = 'ready',               'Ready'
        UNDER_CONSTRUCTION = 'under_construction',  'Under Construction'

    class AreaUnit(models.TextChoices):
        MARLA  = 'marla',  'Marla'
        KANAL  = 'kanal',  'Kanal'
        SQFT   = 'sqft',   'Square Feet'
        SQM    = 'sqm',    'Square Metre'
        ACRE   = 'acre',   'Acre'
        GUNTHA = 'guntha', 'Guntha'
        CENT   = 'cent',   'Cent'

    class Purpose(models.TextChoices):            # FR-LST-001 (T-1070a)
        SALE             = 'sale',             'For Sale'
        RENT_LONG        = 'rent_long',        'For Rent (long term)'
        RENT_SHORT       = 'rent_short',       'For Rent (short term)'
        LEASE_COMMERCIAL = 'lease_commercial', 'Commercial Lease'

    class PriceType(models.TextChoices):          # FR-LST-002 (T-1070a)
        TOTAL     = 'total',     'Total'
        PER_MONTH = 'per_month', 'Per Month'
        PER_YEAR  = 'per_year',  'Per Year'
        PER_SQM   = 'per_sqm',   'Per m²'

    RENT_PURPOSES = frozenset({'rent_long', 'rent_short'})
    PERIODIC_PRICE_TYPES = frozenset({'per_month', 'per_year'})
    ATTRIBUTES_MAX_KEYS  = 50
    ATTRIBUTES_MAX_BYTES = 8192

    class ListingOwnerType(models.TextChoices):
        ORGANIZATION    = 'organization',    'Organization'
        FREELANCE_AGENT = 'freelance_agent', 'Freelance Agent'
        CLIENT          = 'client',          'Client / Individual'
        PLATFORM        = 'platform',        'Platform (Demo/Internal)'

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ref_no        = models.CharField(max_length=30, unique=True, blank=True, db_index=True,
                        help_text='Auto-generated reference number, e.g. RT-LHR-2026-000000001')

    # ── Ownership (traceable to org, freelance agent, or individual) ───────────
    listing_owner_type = models.CharField(
        max_length=20,
        choices=ListingOwnerType.choices,
        default=ListingOwnerType.ORGANIZATION,
        db_index=True,
        help_text='Discriminator: who owns this listing — enforced by DB constraint',
    )
    owner         = models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True, blank=True,
                        related_name='properties',
                        help_text='Set when listing_owner_type=client',
                    )
    organization  = models.ForeignKey(
                        'organizations.Organization',
                        on_delete=models.SET_NULL,
                        null=True, blank=True,
                        related_name='properties',
                        help_text='Set when listing_owner_type=organization',
                    )
    listed_by_agent = models.ForeignKey(
                        'agents.Agent',
                        on_delete=models.SET_NULL,
                        null=True, blank=True,
                        related_name='freelance_listings',
                        help_text='Set when listing_owner_type=freelance_agent',
                    )

    title         = models.CharField(max_length=500)
    description   = models.TextField(blank=True)
    city          = models.CharField(max_length=100)
    location      = models.CharField(max_length=300)

    # Conversion factors to canonical square metres
    _SQM_FACTORS: dict = {
        'marla':  25.2929,
        'kanal':  505.857,
        'sqft':   0.092903,
        'sqm':    1.0,
        'acre':   4046.856,
        'guntha': 101.171,
        'cent':   40.4686,
    }

    # ── Size — unit-agnostic ───────────────────────────────────────────────────
    area_marla    = models.DecimalField(
                        max_digits=10, decimal_places=2, null=True, blank=True,
                        help_text='Numeric size in the unit specified by area_unit',
                    )
    area_unit     = models.CharField(
                        max_length=10,
                        choices=AreaUnit.choices,
                        default=default_area_unit,
                        help_text='Unit for area_marla — marla, kanal, sqft, sqm, acre, etc.',
                    )
    area_sqm      = models.DecimalField(
                        max_digits=12, decimal_places=4, null=True, blank=True,
                        help_text='Canonical area in square metres — auto-computed from area_marla + area_unit on save',
                    )
    # ── Size v2 (T-1070b, ADR-023 / GAP-045) ─────────────────────────────────
    # Canonical names. Kept in sync with the deprecated area_marla/area_unit/area_sqm
    # by save() until the contract step (T-1070g). Nullable: pre-v2 inserts omit them.
    size_value    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                        help_text='Size in size_unit')
    size_unit     = models.CharField(max_length=10, choices=AreaUnit.choices, null=True, blank=True)
    size_m2       = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True,
                        help_text='Canonical size in square metres (computed)')
    plot_size_m2  = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True,
                        help_text='Plot/land area in m² when it differs from the built size')
    latitude      = models.DecimalField(
                        max_digits=9, decimal_places=6,
                        null=True, blank=True,
                        help_text='WGS-84 latitude — enables haversine geo-search',
                    )
    longitude     = models.DecimalField(
                        max_digits=9, decimal_places=6,
                        null=True, blank=True,
                        help_text='WGS-84 longitude — enables haversine geo-search',
                    )

    # ── Price — currency stored alongside (ISO 4217) ───────────────────────────
    price         = models.BigIntegerField(
                        null=True, blank=True,
                        help_text='Price in the currency specified by the currency field',
                    )
    currency      = models.CharField(
                        max_length=3, default=default_currency,
                        help_text='ISO 4217 currency code, e.g. PKR, AED, USD, GBP',
                    )

    property_type        = models.CharField(max_length=30, choices=PropertyType.choices, default=PropertyType.RESIDENTIAL)
    furnished_status     = models.CharField(max_length=20, choices=FurnishedStatus.choices, null=True, blank=True)
    construction_status  = models.CharField(max_length=25, choices=ConstructionStatus.choices, null=True, blank=True)
    legal_status         = models.CharField(max_length=30, choices=LegalStatus.choices, default=LegalStatus.UNVERIFIED)
    assigned_agent = models.ForeignKey(
                         'agents.Agent',
                         on_delete=models.SET_NULL,
                         null=True, blank=True,
                         related_name='assigned_properties',
                         help_text='Agent responsible for selling/renting this property',
                     )
    country       = models.CharField(max_length=2, default=default_country,
                        help_text='ISO 3166-1 alpha-2 country code — used for tax/legal rules')
    market        = models.ForeignKey(
                        'markets.Market', on_delete=models.PROTECT,
                        null=True, blank=True, related_name='properties',
                        help_text='Derived from country on create (ADR-019); NULL = no configured market',
                    )
    installment_available = models.BooleanField(default=False)

    # ── Listing schema v2, expand phase (T-1070a, ADR-023) ─────────────────────
    # New NOT NULL columns carry a DB default so inserts from pre-v2 code keep working.
    purpose        = models.CharField(max_length=20, choices=Purpose.choices,
                         default=Purpose.SALE, db_default=Purpose.SALE)
    bedrooms       = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms      = models.PositiveSmallIntegerField(null=True, blank=True)
    floor          = models.SmallIntegerField(null=True, blank=True,
                         help_text='Floor of the unit; 0 = ground, negative = basement')
    total_floors   = models.PositiveSmallIntegerField(null=True, blank=True)
    year_built     = models.PositiveSmallIntegerField(null=True, blank=True)
    parking_spaces = models.PositiveSmallIntegerField(null=True, blank=True)
    pets_allowed   = models.BooleanField(null=True, blank=True)
    price_type     = models.CharField(max_length=20, choices=PriceType.choices,
                         default=PriceType.TOTAL, db_default=PriceType.TOTAL)
    price_negotiable = models.BooleanField(default=False, db_default=False)
    price_on_request = models.BooleanField(default=False, db_default=False)
    deposit        = models.BigIntegerField(null=True, blank=True,
                         help_text='Security deposit, in `currency`')
    service_charge = models.BigIntegerField(null=True, blank=True,
                         help_text='Service/maintenance charge per year, in `currency`')
    attributes     = models.JSONField(default=dict, db_default={}, blank=True,
                         help_text='Market-specific attributes (validated by the market schema, T-1070f)')
    ai_score      = models.SmallIntegerField(null=True, blank=True)
    risk_level    = models.CharField(max_length=20, choices=RiskLevel.choices, null=True, blank=True)
    raw_docs      = models.JSONField(default=dict, blank=True)
    ai_analysis   = models.JSONField(default=dict, blank=True)
    is_active     = models.BooleanField(default=True)
    is_sandbox    = models.BooleanField(default=True, db_index=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    objects = PropertyQuerySet.as_manager()

    class Meta:
        db_table  = 'properties'
        ordering  = ['-created_at']
        indexes   = [
            models.Index(fields=['city']),
            models.Index(fields=['ai_score']),
            models.Index(fields=['legal_status']),
            models.Index(fields=['listing_owner_type']),
            models.Index(fields=['organization', 'is_active']),
            models.Index(fields=['owner']),
            models.Index(fields=['property_type']),
            models.Index(fields=['purpose', 'is_active'], name='prop_purpose_active_idx'),
            models.Index(fields=['price']),
            models.Index(fields=['is_active', 'city']),
            models.Index(fields=['is_active', 'created_at']),
            # ── Search indexes (T-1100, ADR-015; migration 0022) ──────────────
            # Full-text: the expression must match text_search.search_vector().
            GinIndex(search_vector(), name='prop_fts_gin'),
            # Trigram on UPPER(col): serves Django's icontains/iexact SQL
            # (UPPER(col::text) LIKE/= UPPER(%s)) used by search, recs, API.
            GinIndex(OpClass(Upper('city'), name='gin_trgm_ops'), name='prop_city_trgm'),
            GinIndex(OpClass(Upper('location'), name='gin_trgm_ops'), name='prop_location_trgm'),
            # Ranked consumer search: ORDER BY ai_score DESC, created_at DESC LIMIT n.
            models.Index(
                fields=['-ai_score', '-created_at'],
                condition=models.Q(is_active=True),
                name='prop_active_rank_idx',
            ),
        ]
        constraints = [
            # DB-enforced ownership consistency: each listing_owner_type must
            # have exactly its designated FK populated.
            models.CheckConstraint(
                check=(
                    models.Q(listing_owner_type='organization',    organization__isnull=False) |
                    models.Q(listing_owner_type='freelance_agent', listed_by_agent__isnull=False) |
                    models.Q(listing_owner_type='client',          owner__isnull=False) |
                    models.Q(listing_owner_type='platform')
                ),
                name='property_ownership_consistency',
            ),
        ]

    # Allowed legal_status forward transitions.
    _LEGAL_TRANSITIONS = {
        LegalStatus.UNVERIFIED: {LegalStatus.PENDING, LegalStatus.DISPUTED},
        LegalStatus.PENDING:    {LegalStatus.VERIFIED, LegalStatus.UNVERIFIED, LegalStatus.DISPUTED},
        LegalStatus.VERIFIED:   {LegalStatus.DISPUTED},
        LegalStatus.DISPUTED:   {LegalStatus.PENDING, LegalStatus.UNVERIFIED},
    }

    def clean(self):
        if self.price is not None and self.price <= 0:
            raise ValidationError({'price': 'Price must be a positive value.'})
        errors = self.v2_errors(self.__dict__)
        if errors:
            raise ValidationError(errors)

        if self.pk:
            try:
                old_status = Property.objects.values_list('legal_status', flat=True).get(pk=self.pk)
                if self.legal_status != old_status:
                    allowed = self._LEGAL_TRANSITIONS.get(old_status, set())
                    if self.legal_status not in allowed:
                        raise ValidationError({
                            'legal_status': (
                                f"Invalid transition from '{old_status}' to '{self.legal_status}'. "
                                f"Allowed: {sorted(allowed) or 'none'}."
                            )
                        })
            except Property.DoesNotExist:
                pass

    @classmethod
    def v2_errors(cls, data: dict) -> dict:
        """Cross-field rules for the v2 listing fields (shared by model.clean and the API)."""
        errors = {}
        purpose    = data.get('purpose') or cls.Purpose.SALE
        price_type = data.get('price_type') or cls.PriceType.TOTAL
        if purpose == cls.Purpose.SALE and price_type in cls.PERIODIC_PRICE_TYPES:
            errors['price_type'] = 'A sale price cannot be per month or per year.'
        if data.get('deposit') is not None and data['deposit'] < 0:
            errors['deposit'] = 'Must not be negative.'
        if data.get('service_charge') is not None and data['service_charge'] < 0:
            errors['service_charge'] = 'Must not be negative.'
        floor, total = data.get('floor'), data.get('total_floors')
        if floor is not None and total is not None and floor > total:
            errors['floor'] = 'Must not exceed total_floors.'
        year = data.get('year_built')
        if year is not None and not (1800 <= year <= timezone.now().year + 10):
            errors['year_built'] = 'Must be between 1800 and ten years from now.'
        attrs = data.get('attributes')
        if attrs is not None:
            import json
            if not isinstance(attrs, dict):
                errors['attributes'] = 'Must be an object.'
            elif len(attrs) > cls.ATTRIBUTES_MAX_KEYS or \
                    len(json.dumps(attrs, default=str)) > cls.ATTRIBUTES_MAX_BYTES:
                errors['attributes'] = (f'At most {cls.ATTRIBUTES_MAX_KEYS} keys and '
                                        f'{cls.ATTRIBUTES_MAX_BYTES} bytes.')
        return errors

    def _build_ref_no(self):
        city_key  = self.city.strip().lower()
        city_code = _get_city_code_map().get(city_key, self.city.strip()[:3].upper())
        year      = timezone.now().year
        with transaction.atomic():
            counter, _ = PropertyRefCounter.objects.select_for_update().get_or_create(
                city_code=city_code, year=year, defaults={'last_seq': 0}
            )
            counter.last_seq += 1
            counter.save(update_fields=['last_seq'])
        return f"RT-{city_code}-{year}-{str(counter.last_seq).zfill(9)}"

    def _compute_area_sqm(self):
        if self.area_marla is None:
            return None
        factor = self._SQM_FACTORS.get(self.area_unit or 'marla', 1.0)
        return round(float(self.area_marla) * factor, 4)

    # ── Size v2 sync (T-1070b) ──────────────────────────────────────────────
    _SIZE_V1 = ('area_marla', 'area_unit', 'area_sqm')
    _SIZE_V2 = ('size_value', 'size_unit', 'size_m2')

    @classmethod
    def from_db(cls, db, field_names, values):
        inst = super().from_db(db, field_names, values)
        names = ('area_marla', 'area_unit', 'size_value', 'size_unit')
        # Deferred size fields (.only()): state unknown → the v1 pair wins on save.
        inst._size_loaded = (inst._size_state() if all(n in inst.__dict__ for n in names)
                             else 'deferred')
        return inst

    def _size_state(self):
        d = self.__dict__                    # never triggers a deferred-field fetch
        return (d.get('area_marla'), d.get('area_unit'), d.get('size_value'), d.get('size_unit'))

    def _sync_size(self):
        """
        Keep (size_value, size_unit) and the deprecated (area_marla, area_unit) equal.
        The v2 pair wins when it was changed since load (or set on a new object);
        otherwise the v1 pair wins: it covers pre-v2 writers and un-backfilled rows.
        """
        loaded = getattr(self, '_size_loaded', None)
        v1 = (self.area_marla, self.area_unit)
        v2 = (self.size_value, self.size_unit)
        if loaded == 'deferred':
            v2_changed = False
        elif loaded:
            v2_changed = v2 != loaded[2:]
        else:                                # new, unsaved object
            v2_changed = self.size_value is not None
        if v2_changed:
            self.area_marla = self.size_value
            if self.size_unit:
                self.area_unit = self.size_unit
        elif v2 != v1:
            self.size_value, self.size_unit = v1
        if self.size_unit is None:
            self.size_unit = self.area_unit
        self.area_sqm = self.size_m2 = self._compute_area_sqm()

    def size_in(self, unit: str) -> float | None:
        """Size converted to ``unit`` (an AreaUnit value) via the canonical m²."""
        m2 = self.size_m2 if self.size_m2 is not None else self._compute_area_sqm()
        factor = self._SQM_FACTORS.get(unit)
        if m2 is None or not factor:
            return None
        return round(float(m2) / factor, 2)

    def size_display(self) -> str:
        """'5 marla', '1,200 sqft' — '' when unknown."""
        if self.size_value is None:
            return ''
        v = float(self.size_value)
        num = f"{v:,.0f}" if v.is_integer() else f"{v:,.2f}".rstrip('0').rstrip('.')
        return f"{num} {self.size_unit or self.area_unit}"

    def derive_is_sandbox(self) -> bool:
        """ADR-002: platform demo listings and non-production orgs are sandbox."""
        if self.listing_owner_type == self.ListingOwnerType.PLATFORM:
            return True
        if self.organization_id:
            from apps.organizations.sandbox import org_is_sandbox
            return org_is_sandbox(self.organization)
        # Private client and freelance-agent listings have no sandbox stage.
        return False

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.is_sandbox = self.derive_is_sandbox()
            if self.market_id is None:
                from apps.markets.services import market_code_for_country
                self.market_id = market_code_for_country(self.country)
        if not self.ref_no:
            self.ref_no = self._build_ref_no()
        self._sync_size()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            touched = set(update_fields)
            if touched & set(self._SIZE_V1 + self._SIZE_V2):
                kwargs['update_fields'] = list(touched | set(self._SIZE_V1 + self._SIZE_V2))
        super().save(*args, **kwargs)
        self._size_loaded = self._size_state()

    def __str__(self):
        return f"[{self.ref_no}] {self.title} — {self.city}"


class PropertyImage(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property    = models.ForeignKey(
                      Property,
                      on_delete=models.CASCADE,
                      related_name='images',
                  )
    image       = models.ImageField(upload_to=_property_image_path)
    caption     = models.CharField(max_length=200, blank=True)
    order       = models.PositiveSmallIntegerField(default=0)
    uploaded_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL,
                      on_delete=models.SET_NULL,
                      null=True, blank=True,
                      related_name='uploaded_images',
                  )
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'property_images'
        ordering = ['order', 'created_at']

    def __str__(self):
        return f"Image #{self.order} — {self.property.title}"