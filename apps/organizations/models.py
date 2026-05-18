import uuid
from django.db import models
from django.conf import settings
from django.utils.text import slugify


def _logo_upload_path(instance, filename):
    ext = filename.rsplit('.', 1)[-1].lower()
    return f"organizations/logos/{instance.id}/{uuid.uuid4().hex}.{ext}"


class Organization(models.Model):

    class OrgType(models.TextChoices):
        DEVELOPER      = 'developer',      'Real Estate Developer'
        AGENCY         = 'agency',         'Real Estate Agency'
        BROKERAGE      = 'brokerage',      'Brokerage Firm'
        HOUSING_SOCIETY = 'housing_society', 'Housing Society'
        ENTERPRISE     = 'enterprise',     'Enterprise / Corporate'

    class Plan(models.TextChoices):
        TRIAL        = 'trial',        'Trial'
        BASIC        = 'basic',        'Basic'
        PROFESSIONAL = 'professional', 'Professional'
        ENTERPRISE   = 'enterprise',   'Enterprise'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name       = models.CharField(max_length=200)
    slug       = models.SlugField(max_length=120, unique=True, blank=True,
                     help_text='Auto-generated from name — used in URLs and internal references')
    org_type   = models.CharField(max_length=20, choices=OrgType.choices, default=OrgType.AGENCY)
    admin_user = models.OneToOneField(
                     settings.AUTH_USER_MODEL,
                     on_delete=models.SET_NULL,
                     null=True, blank=True,
                     related_name='owned_organization',
                     help_text='The platform user who administers this organization'
                 )

    # Contact
    phone              = models.CharField(max_length=20, blank=True)
    wa_phone_number_id = models.CharField(
        max_length=50, blank=True, db_index=True,
        help_text="WhatsApp Cloud API phone_number_id for this org's dedicated WA number",
    )
    email   = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    logo    = models.ImageField(upload_to=_logo_upload_path, blank=True, null=True)

    # Location (globally extensible)
    country = models.CharField(max_length=2, default='PK',
                  help_text='ISO 3166-1 alpha-2 country code, e.g. PK, AE, UK')
    city    = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)

    # Plan
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.TRIAL)

    # Status
    is_active   = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False,
                      help_text='Platform admin has verified this organization')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = 'organizations'
        ordering  = ['name']
        indexes   = [
            models.Index(fields=['org_type']),
            models.Index(fields=['country']),
            models.Index(fields=['is_active']),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug = base
            n = 1
            while Organization.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.get_org_type_display()})"


class OrganizationConfig(models.Model):
    """
    Per-organization feature flag / config overrides.
    Resolution order: OrganizationConfig → SystemConfig → SystemConfig.DEFAULTS.
    Only feature_* keys may be set here; platform credentials remain admin-only.
    """

    ALLOWED_KEYS = {
        'feature_property_search',
        'feature_property_listing',
        'feature_tax_advice',
        'feature_loan_eligibility',
        'feature_scam_check',
        'feature_document_verification',
        'feature_property_audit',
        'feature_talk_to_agent',
        'feature_deal_lock',
        'feature_voice_messages',
    }

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='configs',
    )
    key        = models.CharField(max_length=100, db_index=True)
    value      = models.CharField(max_length=20)  # 'true' | 'false'
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='org_config_changes',
    )

    class Meta:
        db_table        = 'organization_configs'
        unique_together = ('organization', 'key')
        ordering        = ['organization', 'key']

    def __str__(self):
        return f"{self.organization.name} / {self.key} = {self.value}"
