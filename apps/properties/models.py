import uuid
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.conf import settings
from django.utils import timezone


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

    # ── Size — unit-agnostic ───────────────────────────────────────────────────
    area_marla    = models.DecimalField(
                        max_digits=10, decimal_places=2, null=True, blank=True,
                        help_text='Numeric size in the unit specified by area_unit',
                    )
    area_unit     = models.CharField(
                        max_length=10,
                        choices=AreaUnit.choices,
                        default=AreaUnit.MARLA,
                        help_text='Unit for area_marla — marla, kanal, sqft, sqm, acre, etc.',
                    )

    # ── Price — currency stored alongside (ISO 4217) ───────────────────────────
    price_pkr     = models.BigIntegerField(
                        null=True, blank=True,
                        help_text='Price in the currency specified by the currency field',
                    )
    currency      = models.CharField(
                        max_length=3, default='PKR',
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
    country       = models.CharField(max_length=2, default='PK',
                        help_text='ISO 3166-1 alpha-2 country code — used for tax/legal rules')
    installment_available = models.BooleanField(default=False)
    ai_score      = models.SmallIntegerField(null=True, blank=True)
    risk_level    = models.CharField(max_length=20, choices=RiskLevel.choices, null=True, blank=True)
    raw_docs      = models.JSONField(default=dict, blank=True)
    ai_analysis   = models.JSONField(default=dict, blank=True)
    is_active     = models.BooleanField(default=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

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
        if self.price_pkr is not None and self.price_pkr <= 0:
            raise ValidationError({'price_pkr': 'Price must be a positive value.'})

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

    def save(self, *args, **kwargs):
        if not self.ref_no:
            self.ref_no = self._build_ref_no()
        super().save(*args, **kwargs)

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