import uuid
from apps.core.crypto import EncryptedJSONField
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from apps.markets.services import default_area_unit, default_country, default_currency  # ADR-019
from django.db.models.functions import Upper
from django.conf import settings
from django.utils import timezone

from . import taxonomy


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

        Published (or under offer), not past ``expires_at`` (the expiry job runs later),
        and in a market that is not inactive (MVP acceptance 3).
        Such listings that are live (``is_sandbox=False``) are always visible.
        Sandbox listings are visible only to their own organization's context, so
        an org can test its bot on its own inventory before go-live.
        """
        live = models.Q(is_sandbox=False)
        viewer_org_id = getattr(viewer_org, 'pk', viewer_org)
        if viewer_org_id:
            live |= models.Q(organization_id=viewer_org_id)
        from apps.markets.models import Market
        inactive = Market.objects.filter(status=Market.Status.INACTIVE).values('code')
        in_market = (models.Q(market__isnull=False) & ~models.Q(market_id__in=inactive)
                     | models.Q(market__isnull=True) & ~models.Q(country__in=inactive))
        unexpired = models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        return self.filter(live, in_market, unexpired, is_active=True)


def _property_image_path(instance, filename):
    # T-1110: the extension comes from the sanitiser's name, never from the client.
    from apps.core.images import safe_upload_name
    return safe_upload_name(f"property_images/{instance.property_id}", filename)


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

    class Category(models.TextChoices):           # FR-LST-001 (T-1070d)
        RESIDENTIAL = 'residential', 'Residential'
        COMMERCIAL  = 'commercial',  'Commercial'
        INDUSTRIAL  = 'industrial',  'Industrial'
        LAND        = 'land',        'Land'
        HOSPITALITY = 'hospitality', 'Hospitality'

    SUBTYPE_CHOICES = [(s, taxonomy.subtype_label(s))
                       for subs in taxonomy.SUBTYPES.values() for s in subs]
    MAX_AMENITIES = 60

    RENT_PURPOSES = frozenset({'rent_long', 'rent_short'})
    PERIODIC_PRICE_TYPES = frozenset({'per_month', 'per_year'})
    ATTRIBUTES_MAX_KEYS  = 50
    ATTRIBUTES_MAX_BYTES = 8192

    class Status(models.TextChoices):             # FR-LST-005 (T-1080)
        DRAFT          = 'draft',          'Draft'
        PENDING_REVIEW = 'pending_review', 'Pending review'
        PUBLISHED      = 'published',      'Published'
        UNDER_OFFER    = 'under_offer',    'Under offer'
        SOLD           = 'sold',           'Sold'
        LET            = 'let',            'Let'
        EXPIRED        = 'expired',        'Expired'
        ARCHIVED       = 'archived',       'Archived'
        SUSPENDED      = 'suspended',      'Suspended'

    # Statuses shown publicly; ``is_active`` == status in VISIBLE_STATUSES
    # (kept in step by the 0045 trigger and by save()).
    VISIBLE_STATUSES = frozenset({'published', 'under_offer'})

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
    # ── Location hierarchy (T-1060a, ADR-024). city/location stay as the
    # listed text; location_ref is the matched node (set by the API, the
    # match_listing_locations command and the hourly matcher, not by save()).
    location_ref  = models.ForeignKey('locations.Location', on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='listings',
                                      db_index=False)       # index built concurrently (0039)
    location_precision = models.CharField(
                        max_length=12, choices=[('exact', 'Exact'), ('approximate', 'Approximate')],
                        default='approximate', db_default='approximate',
                        help_text='exact = coordinates are the property itself (FR-LST-003)')

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

    # ── Size (T-1070b; legacy area_marla/area_unit/area_sqm removed from the
    # model in T-1070g-1, mirrored by a DB trigger until the columns drop in g-2)
    size_value    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                        help_text='Size in size_unit')
    size_unit     = models.CharField(max_length=10, choices=AreaUnit.choices, null=True, blank=True,
                        default=default_area_unit)
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
    # Public point (T-1060b, ADR-024 addendum): what geo search and other
    # viewers see. A DB trigger fills it from latitude/longitude, snapped to 2
    # decimals (~1 km) for private-seller or approximate listings, so exact
    # coordinates never answer a public query (no radius triangulation).
    geo_lat       = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                        editable=False)
    geo_lng       = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                        editable=False)

    # ── Price — currency stored alongside (ISO 4217) ───────────────────────────
    price         = models.BigIntegerField(
                        null=True, blank=True,
                        help_text='Price in the currency specified by the currency field',
                    )
    currency      = models.CharField(
                        max_length=3, default=default_currency,
                        help_text='ISO 4217 currency code, e.g. PKR, AED, USD, GBP',
                    )

    # ── Taxonomy (T-1070d). The legacy property_type column left the model in
    # T-1070g-1 (a DB trigger mirrors it for older pods until g-2 drops it);
    # ``property_type`` below is a read-only derived value.
    category             = models.CharField(max_length=20, choices=Category.choices, null=True, blank=True,
                                            default=Category.RESIDENTIAL)
    subtype              = models.CharField(max_length=40, choices=SUBTYPE_CHOICES, null=True, blank=True)
    amenities            = models.ManyToManyField('Amenity', through='ListingAmenity',
                                                  related_name='listings', blank=True)
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
    # Stored full-text document (T-1070f, KI-035). Maintained only by the DB
    # trigger from migration 0035 (also for QuerySet.update and pre-v2 pods);
    # application code never writes it.
    search_tsv    = SearchVectorField(null=True, editable=False)
    ai_score      = models.SmallIntegerField(null=True, blank=True)
    risk_level    = models.CharField(max_length=20, choices=RiskLevel.choices, null=True, blank=True)
    raw_docs      = EncryptedJSONField(default=dict, blank=True)
    ai_analysis   = models.JSONField(default=dict, blank=True)
    # Derived from ``status`` (T-1080): readers keep using it as "publicly visible".
    is_active     = models.BooleanField(default=True)
    # Lifecycle (T-1080, ADR-025). Change it through apps.properties.lifecycle.
    status             = models.CharField(max_length=20, choices=Status.choices,
                                          default=Status.PUBLISHED, db_default='published')
    status_changed_at  = models.DateTimeField(null=True, blank=True)
    published_at       = models.DateTimeField(null=True, blank=True)
    expires_at         = models.DateTimeField(null=True, blank=True)
    expiry_reminded_at = models.DateTimeField(null=True, blank=True)
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
            models.Index(fields=['purpose', 'is_active'], name='prop_purpose_active_idx'),
            models.Index(fields=['category', 'is_active'], name='prop_category_active_idx'),
            models.Index(fields=['location_ref', 'is_active'], name='prop_location_ref_idx'),
            models.Index(fields=['geo_lat', 'geo_lng'], name='prop_geo_point_idx'),   # 0044
            models.Index(fields=['price']),
            models.Index(fields=['is_active', 'city']),
            models.Index(fields=['is_active', 'created_at']),
            # Lifecycle (T-1080; 0047, concurrently): owner dashboards and the expiry job.
            models.Index(fields=['organization', 'status'], name='prop_org_status_idx'),
            models.Index(fields=['expires_at'], condition=models.Q(status='published'),
                         name='prop_expiry_idx'),
            # ── Search indexes (T-1100, ADR-015; migration 0022) ──────────────
            # Full-text on the trigger-maintained search_tsv (T-1070f, 0037).
            # The legacy expression index prop_fts_gin left the model in
            # T-1070g-1 and is dropped concurrently in g-2.
            GinIndex(fields=['search_tsv'], name='prop_search_tsv_gin'),
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
            # DB-03: allowed status values (0045 adds it NOT VALID, then validates).
            models.CheckConstraint(
                condition=models.Q(status__in=['draft', 'pending_review', 'published', 'under_offer',
                                           'sold', 'let', 'expired', 'archived', 'suspended']),
                name='property_status_valid',
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
        if 'attributes' not in errors:
            from apps.markets.services import market_code_for_country
            market = self.market_id or market_code_for_country(self.country)
            if (attr_errors := self.attribute_errors(self.attributes, market)):
                errors['attributes'] = [f'{k}: {v}' for k, v in sorted(attr_errors.items())]
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
        category = data.get('category') or cls.Category.RESIDENTIAL
        errors.update(taxonomy.subtype_errors(category, data.get('subtype')))
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

    @staticmethod
    def attribute_schema_for(market_code: str | None) -> dict:
        """The market's listing attribute schema (T-1070e); {} = no market or no rules."""
        if not market_code:
            return {}
        from apps.markets.services import get_market
        info = get_market(market_code)
        return dict(getattr(info, 'listing_schema', None) or {}) if info else {}

    @classmethod
    def attribute_errors(cls, attributes, market_code: str | None) -> dict:
        """Per-key errors for ``attributes`` under the market schema (FR-LST-004)."""
        from apps.markets.listing_schema import attribute_errors
        return attribute_errors(cls.attribute_schema_for(market_code), attributes or {})

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

    def _compute_size_m2(self):
        """size_value × unit factor, as a Decimal with the column's 4 places (KI-051)."""
        if self.size_value is None:
            return None
        from decimal import ROUND_HALF_UP, Decimal
        factor = Decimal(str(self._SQM_FACTORS.get(self.size_unit or 'marla', 1.0)))
        return (Decimal(str(self.size_value)) * factor).quantize(Decimal('0.0001'), ROUND_HALF_UP)

    @property
    def property_type(self) -> str:
        """Legacy coarse type, derived from category (land → plot, hospitality → commercial)."""
        return taxonomy.property_type_for_category(self.category) or self.PropertyType.RESIDENTIAL

    @classmethod
    def from_db(cls, db, field_names, values):
        inst = super().from_db(db, field_names, values)
        inst._price_loaded = inst._price_state()
        inst._country_loaded = inst.__dict__.get('country', 'deferred')
        inst._status_loaded = (inst.__dict__.get('status'), inst.__dict__.get('is_active'))
        return inst

    def _sync_status(self, update_fields):
        """
        Keep ``status`` and ``is_active`` consistent (same rule as the 0045
        trigger). ``status`` wins when it changed; a write that changed only
        ``is_active`` (legacy code) maps to published / archived. Returns the
        previous status when it changed (for the audit row), else ``None``.
        """
        if 'status' not in self.__dict__ or 'is_active' not in self.__dict__:
            return None                                   # deferred: the trigger decides
        if update_fields is not None and not {'status', 'is_active'} & set(update_fields):
            return None
        visible = self.VISIBLE_STATUSES
        loaded = getattr(self, '_status_loaded', None)
        if self._state.adding or loaded is None or loaded[0] is None:
            if self.status in visible and not self.is_active:
                self.status = self.Status.ARCHIVED          # legacy create(is_active=False)
            prev = '' if self._state.adding else None
        else:
            prev_status, prev_active = loaded
            if self.status == prev_status and self.is_active != prev_active:
                if self.is_active and self.status not in visible:
                    self.status = self.Status.PUBLISHED
                elif not self.is_active and self.status in visible:
                    self.status = self.Status.ARCHIVED
            prev = prev_status if self.status != prev_status else None
        self.is_active = self.status in visible
        if prev is not None:
            self.status_changed_at = timezone.now()
        return prev

    _SIZE_FIELDS = ('size_value', 'size_unit', 'size_m2')
    _TAXONOMY_FIELDS = ('category', 'subtype')

    _GEO_INPUTS = ('latitude', 'longitude', 'listing_owner_type', 'location_precision',
                   'location_ref_id')

    def _public_point(self):
        """Same rule as the public-point trigger (apps.geo.sql v2), for the in-memory instance."""
        from decimal import ROUND_HALF_UP, Decimal

        def snap(v):
            return Decimal(str(v)).quantize(Decimal('0.01'), ROUND_HALF_UP).quantize(Decimal('0.000001'))

        if self.latitude is None or self.longitude is None:
            if not self.location_ref_id:
                return None, None
            from apps.locations.models import Location   # centroid fallback (T-1060c)
            c = Location.objects.filter(pk=self.location_ref_id).values_list('latitude', 'longitude').first()
            if not c or c[0] is None or c[1] is None:
                return None, None
            return snap(c[0]), snap(c[1])
        if self.listing_owner_type == self.ListingOwnerType.CLIENT or self.location_precision != 'exact':
            return snap(self.latitude), snap(self.longitude)
        return self.latitude, self.longitude

    def _normalise_size_and_taxonomy(self):
        """size_m2 follows size_value/size_unit; a subtype outside the category is cleared."""
        d = self.__dict__
        if 'size_value' in d and 'size_unit' in d:
            self.size_m2 = self._compute_size_m2()
        if 'category' in d and 'subtype' in d and self.subtype and \
                taxonomy.subtype_errors(self.category, self.subtype):
            self.subtype = None

    # ── Price history (T-1070c) ─────────────────────────────────────────────
    _PRICE_FIELDS = ('price', 'currency', 'price_type', 'price_on_request')

    def _price_state(self):
        d = self.__dict__
        if not all(f in d for f in self._PRICE_FIELDS):
            return 'deferred'
        return tuple(d[f] for f in self._PRICE_FIELDS)

    def _record_price_change(self, adding: bool, update_fields) -> None:
        """Append a PriceHistory row when the saved price terms differ from the last one."""
        if update_fields is not None and not set(update_fields) & set(self._PRICE_FIELDS):
            return
        state = self._price_state()
        if state == 'deferred':
            return                                   # price terms were not loaded or set
        if adding:
            if self.price is None and not self.price_on_request:
                return
        else:
            loaded = getattr(self, '_price_loaded', 'deferred')
            if loaded != 'deferred' and loaded == state:
                return
            last = (self.price_history.order_by('-changed_at', '-id')
                    .values_list(*self._PRICE_FIELDS).first())
            if last == state:
                return                               # e.g. deferred load, value unchanged
        from .price_history import current_actor, current_source
        PriceHistory.objects.create(
            property=self, price=self.price, currency=self.currency,
            price_type=self.price_type, price_on_request=self.price_on_request,
            changed_by=current_actor(), source=current_source(),
        )

    def size_in(self, unit: str) -> float | None:
        """Size converted to ``unit`` (an AreaUnit value) via the canonical m²."""
        m2 = self.size_m2 if self.size_m2 is not None else self._compute_size_m2()
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
        return f"{num} {self.size_unit or ''}".strip()

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
        elif self._country_changed(kwargs.get('update_fields')):
            # KI-050: a listing moved to another country follows that market.
            from apps.markets.services import market_code_for_country
            self.market_id = market_code_for_country(self.country)
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'market'})
        if not self.ref_no:
            self.ref_no = self._build_ref_no()
        self._normalise_size_and_taxonomy()
        prev_status = self._sync_status(kwargs.get('update_fields'))
        geo_computed = all(f in self.__dict__ for f in self._GEO_INPUTS)
        if geo_computed:
            self.geo_lat, self.geo_lng = self._public_point()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            touched = set(update_fields)
            if geo_computed and touched & (set(self._GEO_INPUTS) | {'location_ref'}):
                touched |= {'geo_lat', 'geo_lng'}     # (the trigger recomputes them anyway)
            if touched & {'size_value', 'size_unit'}:
                touched.add('size_m2')
            if 'category' in touched:
                touched.add('subtype')
            if touched & {'status', 'is_active'}:
                touched |= {'status', 'is_active'}
                if prev_status is not None:
                    touched.add('status_changed_at')
            kwargs['update_fields'] = list(touched)
        adding = self._state.adding
        with transaction.atomic():                   # listing + its price-history row together
            super().save(*args, **kwargs)
            self._record_price_change(adding, update_fields)
            if prev_status is not None:
                self._record_transition(prev_status)
        # Refresh a snapshot only when its fields were written (else it would
        # mark unsaved in-memory values as persisted).
        written = None if update_fields is None else set(kwargs['update_fields'])
        if written is None or written & set(self._PRICE_FIELDS):
            self._price_loaded = self._price_state()
        if written is None or 'country' in written:
            self._country_loaded = self.__dict__.get('country', 'deferred')
        if written is None or 'status' in written:
            self._status_loaded = (self.__dict__.get('status'), self.__dict__.get('is_active'))

    def _record_transition(self, prev_status: str) -> None:
        """Audit row for every status change made through the ORM (T-1080)."""
        ctx = getattr(self, '_transition_ctx', None) or {}
        ListingTransition.objects.create(
            property=self, from_status=prev_status, to_status=self.status,
            actor=ctx.get('actor'),
            source=ctx.get('source') or (ListingTransition.Source.CREATE if prev_status == ''
                                         else ListingTransition.Source.DIRECT),
            reason=(ctx.get('reason') or '')[:500])
        self._transition_ctx = None

    def _country_changed(self, update_fields) -> bool:
        if update_fields is not None and 'country' not in update_fields:
            return False
        if 'country' not in self.__dict__:
            return False                             # deferred and never assigned
        loaded = getattr(self, '_country_loaded', 'deferred')
        return loaded == 'deferred' or loaded != self.country

    def __str__(self):
        return f"[{self.ref_no}] {self.title} — {self.city}"


class PriceHistory(models.Model):
    """Append-only log of a listing's price terms (FR-LST-002, T-1070c)."""

    class Source(models.TextChoices):
        API      = 'api',      'API'
        ADMIN    = 'admin',    'Admin'
        AI_TOOL  = 'ai_tool',  'AI assistant'
        SYSTEM   = 'system',   'System'
        BACKFILL = 'backfill', 'Backfill'

    property         = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='price_history')
    price            = models.BigIntegerField(null=True, blank=True)
    currency         = models.CharField(max_length=3)
    price_type       = models.CharField(max_length=20, choices=Property.PriceType.choices)
    price_on_request = models.BooleanField(default=False)
    changed_at       = models.DateTimeField(default=timezone.now)
    changed_by       = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='+')
    source           = models.CharField(max_length=10, choices=Source.choices, default=Source.SYSTEM)

    class Meta:
        db_table = 'property_price_history'
        ordering = ['-changed_at', '-id']
        indexes  = [models.Index(fields=['property', '-changed_at'], name='price_hist_prop_idx')]

    def __str__(self):
        return f"{self.property_id} {self.currency} {self.price} @ {self.changed_at:%Y-%m-%d}"


class ListingTransition(models.Model):
    """Append-only audit of listing status changes (FR-LST-005, T-1080)."""

    class Source(models.TextChoices):
        API    = 'api',    'API'
        ADMIN  = 'admin',  'Admin'
        SYSTEM = 'system', 'System (expiry)'
        CREATE = 'create', 'Created'
        DIRECT = 'direct', 'Direct write (legacy path)'

    property    = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='transitions')
    from_status = models.CharField(max_length=20, blank=True)        # '' = created
    to_status   = models.CharField(max_length=20, choices=Property.Status.choices)
    actor       = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    source      = models.CharField(max_length=10, choices=Source.choices, default=Source.DIRECT)
    reason      = models.CharField(max_length=500, blank=True)
    created_at  = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'property_transitions'
        ordering = ['-created_at', '-id']
        indexes  = [models.Index(fields=['property', '-created_at'], name='prop_transition_idx')]

    def __str__(self):
        return f"{self.property_id} {self.from_status or '∅'} → {self.to_status}"


class Amenity(models.Model):
    """Amenity taxonomy entry (T-1070d). Seeded by migration 0033; editable in admin."""

    class Group(models.TextChoices):
        INTERIOR      = 'interior',      'Interior'
        BUILDING      = 'building',      'Building'
        OUTDOOR       = 'outdoor',       'Outdoor'
        SECURITY      = 'security',      'Security'
        UTILITIES     = 'utilities',     'Utilities'
        COMMUNITY     = 'community',     'Community'
        ACCESSIBILITY = 'accessibility', 'Accessibility'

    key        = models.SlugField(max_length=50, unique=True,
                                  help_text='Stable API identifier, e.g. "gym"')
    group      = models.CharField(max_length=20, choices=Group.choices)
    labels     = models.JSONField(default=dict, help_text='Label per language: {"en": "Gym", "ur": "…"}')
    markets    = models.JSONField(default=list, blank=True,
                                  help_text='Market codes where it applies; empty = every market')
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active  = models.BooleanField(default=True)

    class Meta:
        db_table = 'amenities'
        ordering = ['group', 'sort_order', 'key']

    def clean(self):
        if not isinstance(self.labels, dict) or not self.labels.get('en'):
            raise ValidationError({'labels': 'Needs at least an "en" label.'})
        if not isinstance(self.markets, list) or not all(
                isinstance(m, str) and len(m) == 2 and m.isupper() for m in self.markets):
            raise ValidationError({'markets': 'A list of two-letter market codes.'})

    def label(self, lang: str = 'en') -> str:
        return (self.labels or {}).get(lang) or (self.labels or {}).get('en') or self.key

    def available_in(self, market_code: str | None) -> bool:
        return not self.markets or not market_code or market_code in self.markets

    def __str__(self):
        return self.key


class ListingAmenity(models.Model):
    """A listing has an amenity (T-1070d)."""
    property   = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='listing_amenities')
    amenity    = models.ForeignKey(Amenity, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'property_amenities'
        constraints = [models.UniqueConstraint(fields=['property', 'amenity'],
                                               name='uniq_property_amenity')]
        indexes = [models.Index(fields=['amenity', 'property'], name='prop_amenity_lookup_idx')]


class PropertyImage(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property    = models.ForeignKey(
                      Property,
                      on_delete=models.CASCADE,
                      related_name='images',
                  )
    image       = models.ImageField(upload_to=_property_image_path)
    # T-1110: generated by apps.core.images (re-encoded, metadata stripped).
    thumbnail   = models.ImageField(upload_to=_property_image_path, blank=True, null=True)
    caption     = models.CharField(max_length=200, blank=True)
    alt_text    = models.CharField(max_length=250, blank=True, default='', db_default='')
    width       = models.PositiveIntegerField(null=True, blank=True)
    height      = models.PositiveIntegerField(null=True, blank=True)
    size_bytes  = models.PositiveIntegerField(null=True, blank=True)
    # sha256 of the stored bytes ('' = uploaded before T-1110, not yet sanitised).
    sha256      = models.CharField(max_length=64, blank=True, default='', db_default='')
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