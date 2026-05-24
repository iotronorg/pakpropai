import uuid
from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.utils import timezone


def _logo_upload_path(instance, filename):
    ext = filename.rsplit('.', 1)[-1].lower()
    return f"organizations/logos/{instance.id}/{uuid.uuid4().hex}.{ext}"


class Organization(models.Model):

    class OrgType(models.TextChoices):
        DEVELOPER             = 'developer',             'Real Estate Developer'
        AGENCY                = 'agency',                'Real Estate Agency'
        BROKERAGE             = 'brokerage',             'Brokerage Firm'
        COMMUNITY_DEVELOPMENT = 'community_development', 'Community Development'
        HOUSING_SOCIETY       = 'housing_society',       'Housing Society (deprecated)'
        ENTERPRISE            = 'enterprise',            'Enterprise / Corporate'

    class Plan(models.TextChoices):
        TRIAL        = 'trial',        'Trial'
        BASIC        = 'basic',        'Basic'
        PROFESSIONAL = 'professional', 'Professional'
        ENTERPRISE   = 'enterprise',   'Enterprise'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name       = models.CharField(max_length=200)
    slug       = models.SlugField(max_length=120, unique=True, blank=True,
                     help_text='Auto-generated from name — used in URLs and internal references')
    org_type   = models.CharField(max_length=25, choices=OrgType.choices, default=OrgType.AGENCY)
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
    brand_color = models.CharField(
        max_length=7,
        default='#1B4F72',
        blank=True,
        help_text='Hex color code for PDF report header, e.g. #1B4F72',
    )

    class Language(models.TextChoices):
        ENGLISH  = 'en', 'English'
        ARABIC   = 'ar', 'Arabic'
        URDU     = 'ur', 'Urdu'
        FRENCH   = 'fr', 'French'
        CHINESE  = 'zh', 'Chinese (Simplified)'
        SPANISH  = 'es', 'Spanish'

    class MeasurementSystem(models.TextChoices):
        PK_TRADITIONAL = 'pk_traditional', 'Pakistan Traditional (Marla / Kanal)'
        IMPERIAL       = 'imperial',       'Imperial (Square Feet)'
        METRIC         = 'metric',         'Metric (Square Metres)'

    # Location (globally extensible)
    country  = models.CharField(max_length=2, default='PK',
                   help_text='ISO 3166-1 alpha-2 country code, e.g. PK, AE, GB, US')
    language = models.CharField(
                   max_length=5, choices=Language.choices, default=Language.ENGLISH,
                   help_text='Preferred AI response language for this org\'s clients',
               )
    measurement_system = models.CharField(
                   max_length=20, choices=MeasurementSystem.choices,
                   default=MeasurementSystem.PK_TRADITIONAL,
                   help_text='Primary area unit for property listings in this org',
               )

    _EU_COUNTRIES = frozenset({
        'DE', 'FR', 'NL', 'BE', 'ES', 'IT', 'SE', 'NO', 'DK',
        'FI', 'AT', 'CH', 'PT', 'IE', 'PL', 'CZ', 'HU', 'RO',
    })

    class DataResidencyRegion(models.TextChoices):
        GLOBAL = 'global', 'Global'
        EU     = 'eu',     'European Union'
        UK     = 'uk',     'United Kingdom'
        UAE    = 'uae',    'UAE'
        PK     = 'pk',     'Pakistan'

    data_residency_region = models.CharField(
        max_length=10,
        choices=DataResidencyRegion.choices,
        default=DataResidencyRegion.GLOBAL,
        help_text='Auto-set from country at creation. Drives GDPR module and future DB routing.',
    )

    city     = models.CharField(max_length=100, blank=True)
    address  = models.TextField(blank=True)

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
        if not self.data_residency_region or self.data_residency_region == 'global':
            if self.country == 'GB':
                self.data_residency_region = self.DataResidencyRegion.UK
            elif self.country == 'AE':
                self.data_residency_region = self.DataResidencyRegion.UAE
            elif self.country == 'PK':
                self.data_residency_region = self.DataResidencyRegion.PK
            elif self.country in self._EU_COUNTRIES:
                self.data_residency_region = self.DataResidencyRegion.EU
            else:
                self.data_residency_region = self.DataResidencyRegion.GLOBAL
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


class OrgPaymentSettings(models.Model):
    """
    Per-org deal-lock payment gateway configuration.
    Overrides the platform-level SystemConfig for client→org transactions.
    """

    class Gateway(models.TextChoices):
        SAFEPAY   = 'safepay',   'Safepay'
        BSECURE   = 'bsecure',   'bSecure'
        MANUAL    = 'manual',    'Manual (bank transfer / JazzCash / EasyPaisa)'

    SENSITIVE_FIELDS = {
        'safepay_secret_key', 'bsecure_client_secret',
    }

    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name='payment_settings',
    )
    gateway = models.CharField(
        max_length=20, choices=Gateway.choices, default=Gateway.MANUAL
    )

    # Safepay merchant credentials
    safepay_merchant_key = models.CharField(max_length=200, blank=True)
    safepay_secret_key   = models.CharField(max_length=200, blank=True)
    safepay_environment  = models.CharField(
        max_length=20,
        choices=[('sandbox', 'Sandbox'), ('production', 'Production')],
        default='sandbox',
    )

    # bSecure credentials
    bsecure_client_id     = models.CharField(max_length=200, blank=True)
    bsecure_client_secret = models.CharField(max_length=200, blank=True)
    bsecure_environment   = models.CharField(
        max_length=20,
        choices=[('sandbox', 'Sandbox'), ('production', 'Production')],
        default='sandbox',
    )

    # Manual payment details (overrides platform defaults)
    jazzcash_number    = models.CharField(max_length=20, blank=True)
    easypaisa_number   = models.CharField(max_length=20, blank=True)
    bank_account_number = models.CharField(max_length=50, blank=True)
    bank_account_name   = models.CharField(max_length=200, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'org_payment_settings'

    def __str__(self):
        return f"{self.organization.name} — {self.gateway}"


class OrganizationMembership(models.Model):
    """
    Explicit user ↔ organization association table.

    Currently populated via data migration from Agent.organization and
    Organization.admin_user. When USE_MEMBERSHIP_RBAC is enabled,
    querysets will switch from User.agent_profile.organization to this table.
    """

    class Role(models.TextChoices):
        OWNER           = 'owner',           'Owner'
        ORG_ADMIN       = 'org_admin',       'Organization Admin'
        TEAM_MANAGER    = 'team_manager',    'Team Manager'
        SALES_MANAGER   = 'sales_manager',   'Sales Manager'
        CRM_OPERATOR    = 'crm_operator',    'CRM Operator'
        AGENT           = 'agent',           'Agent'
        FREELANCE_AGENT = 'freelance_agent', 'Freelance Agent'
        VIEWER          = 'viewer',          'Viewer'

    class EmploymentType(models.TextChoices):
        INTERNAL  = 'internal',  'Internal'
        FREELANCE = 'freelance', 'Freelance'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    role            = models.CharField(max_length=20, choices=Role.choices)
    employment_type = models.CharField(
        max_length=20, choices=EmploymentType.choices, default=EmploymentType.INTERNAL
    )
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table        = 'organization_memberships'
        unique_together = [('user', 'organization')]
        indexes         = [
            models.Index(fields=['organization', 'is_active']),
            models.Index(fields=['user', 'is_active']),
        ]

    def deactivate(self):
        self.is_active = False
        self.left_at   = timezone.now()
        self.save(update_fields=['is_active', 'left_at'])

    def __str__(self):
        return f"{self.user} → {self.organization.name} [{self.role}]"
