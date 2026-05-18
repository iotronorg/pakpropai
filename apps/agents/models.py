from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings


class Agent(models.Model):

    class AgentType(models.TextChoices):
        INDIVIDUAL = 'individual', 'Individual Agent'
        DEVELOPER  = 'developer',  'Real Estate Developer'
        AGENCY     = 'agency',     'Agency / Organization'

    class EmploymentType(models.TextChoices):
        INTERNAL  = 'internal',  'Internal (Employee)'
        FREELANCE = 'freelance', 'Freelance (Independent)'

    class Specialization(models.TextChoices):
        RESIDENTIAL_BUY  = 'residential_buy',  'Residential — Buy/Sell'
        RESIDENTIAL_RENT = 'residential_rent', 'Residential — Rent/Lease'
        COMMERCIAL       = 'commercial',        'Commercial Properties'
        PLOTS            = 'plots',             'Plots & Land'
        NEW_PROJECTS     = 'new_projects',      'New Projects / Off-plan'
        LUXURY           = 'luxury',            'Luxury / High-end'
        INDUSTRIAL       = 'industrial',        'Industrial / Warehouse'

    # ── System User Link ──────────────────────────────────────────────────────
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='agent_profile',
        help_text='Dashboard login account for this agent'
    )
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='agents',
        help_text='Organization this agent belongs to (null = independent freelance agent)'
    )
    employment_type = models.CharField(
        max_length=20,
        choices=EmploymentType.choices,
        default=EmploymentType.FREELANCE,
        help_text='Internal = employed by the organization; Freelance = independent contractor'
    )
    # ── Identity ──────────────────────────────────────────────────────────────
    name            = models.CharField(max_length=200)
    agent_type      = models.CharField(
                          max_length=20, choices=AgentType.choices,
                          default=AgentType.INDIVIDUAL
                      )
    phone           = models.CharField(max_length=20, unique=True,
                          help_text='Primary contact number (with country code, e.g. +923001234567)')
    whatsapp_number = models.CharField(max_length=20, blank=True,
                          help_text='WhatsApp number if different from phone')
    email           = models.EmailField(blank=True)
    cnic_number     = models.CharField(max_length=20, blank=True,
                          help_text='For individual agents only (format: 12345-1234567-1)')
    profile_photo   = models.ImageField(upload_to='agents/photos/', blank=True, null=True)

    # ── Professional Details ───────────────────────────────────────────────────
    company_name     = models.CharField(max_length=200, blank=True,
                           help_text='For agencies and developers: official company/brand name')
    designation      = models.CharField(max_length=100, blank=True,
                           help_text='e.g. Senior Property Consultant, Director, Branch Manager')
    license_number   = models.CharField(max_length=100, blank=True,
                           help_text='REAP / PBTE / local authority registration number')
    years_experience = models.PositiveSmallIntegerField(default=0,
                           help_text='Years of active experience in real estate')
    languages        = models.JSONField(default=list,
                           help_text='Languages spoken, e.g. ["Urdu", "English", "Punjabi"]')
    bio              = models.TextField(blank=True,
                           help_text='Short professional bio shown to users (2-4 sentences)')

    # ── Specializations & Geographic Coverage ─────────────────────────────────
    specializations = models.JSONField(default=list,
                          help_text=(
                              'List of specialization keys: residential_buy, residential_rent, '
                              'commercial, plots, new_projects, luxury, industrial'
                          ))
    cities          = models.JSONField(default=list,
                          help_text='Cities covered, e.g. ["Lahore", "Islamabad"]')
    areas           = models.JSONField(default=list,
                          help_text='Specific areas/societies, e.g. ["DHA Phase 5", "Gulberg III", "F-7"]')
    primary_city    = models.CharField(max_length=100, blank=True,
                          help_text='Main city of operation (used for priority matching)')

    # ── Business Information (Agencies / Developers) ──────────────────────────
    registration_number = models.CharField(max_length=100, blank=True,
                              help_text='SECP company registration or chamber of commerce number')
    ntn_number          = models.CharField(max_length=50, blank=True,
                              help_text='FBR National Tax Number')
    website             = models.URLField(blank=True)
    office_address      = models.TextField(blank=True,
                              help_text='Full office/branch address')
    instagram_handle    = models.CharField(max_length=100, blank=True,
                              help_text='Instagram username (without @)')
    facebook_page       = models.URLField(blank=True,
                              help_text='Facebook page URL')

    # ── Availability ─────────────────────────────────────────────────────────
    class AvailabilityStatus(models.TextChoices):
        AVAILABLE = 'available', 'Available'
        BUSY      = 'busy',      'Busy'
        OFFLINE   = 'offline',   'Offline'

    availability_status = models.CharField(
        max_length=20,
        choices=AvailabilityStatus.choices,
        default=AvailabilityStatus.AVAILABLE,
        help_text='Real-time availability shown to leads and used in auto-assignment',
    )

    # ── Registration & Approval ───────────────────────────────────────────────
    class RegistrationStatus(models.TextChoices):
        PENDING  = 'pending',  'Pending Approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    registration_status = models.CharField(
        max_length=20,
        choices=RegistrationStatus.choices,
        default=RegistrationStatus.PENDING,
        help_text='Approval state — pending until admin/developer reviews the application',
    )
    rejection_reason = models.TextField(
        blank=True,
        help_text='Reason shown to the agent when their application is rejected',
    )

    # ── Status & Verification ─────────────────────────────────────────────────
    is_verified  = models.BooleanField(default=False,
                       help_text='Admin has verified identity and credentials')
    is_active    = models.BooleanField(default=False,
                       help_text='Uncheck to temporarily disable this agent from receiving leads')
    is_featured  = models.BooleanField(default=False,
                       help_text='Featured agents appear first in all matches')
    verified_at  = models.DateTimeField(null=True, blank=True)
    verified_by  = models.ForeignKey(
                       settings.AUTH_USER_MODEL, null=True, blank=True,
                       on_delete=models.SET_NULL, related_name='verified_agents',
                       help_text='Admin user who verified this agent'
                   )
    internal_notes = models.TextField(blank=True,
                         help_text='Admin-only notes — not shown to users')

    # ── Performance Metrics ────────────────────────────────────────────────────
    total_leads    = models.PositiveIntegerField(default=0,
                         help_text='Total leads routed to this agent')
    total_listings = models.PositiveIntegerField(default=0,
                         help_text='Number of active/historical listings')
    closed_deals   = models.PositiveIntegerField(default=0,
                         help_text='Successfully closed transactions')
    rating         = models.DecimalField(max_digits=3, decimal_places=1, default=0.0,
                         help_text='Average user rating out of 5.0')
    last_active_at = models.DateTimeField(null=True, blank=True,
                         help_text='Last time a lead was sent to this agent')

    joined_at  = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = 'agents'
        ordering  = ['-is_featured', '-is_verified', '-rating', 'name']
        verbose_name        = 'Agent'
        verbose_name_plural = 'Agents'

    @property
    def contact_whatsapp(self) -> str:
        return self.whatsapp_number or self.phone

    @property
    def display_name(self) -> str:
        if self.company_name:
            return f"{self.name} — {self.company_name}"
        return self.name

    @property
    def cities_str(self) -> str:
        return ', '.join(self.cities) if self.cities else '—'

    @property
    def specializations_str(self) -> str:
        labels = {
            'residential_buy':  'Buy/Sell',
            'residential_rent': 'Rent/Lease',
            'commercial':       'Commercial',
            'plots':            'Plots',
            'new_projects':     'Off-plan',
            'luxury':           'Luxury',
            'industrial':       'Industrial',
        }
        return ', '.join(labels.get(s, s) for s in self.specializations) if self.specializations else '—'

    def clean(self):
        if self.primary_city and self.cities and self.primary_city not in self.cities:
            raise ValidationError({
                'primary_city': (
                    f"'{self.primary_city}' is not in the agent's cities list. "
                    "Add it to cities first, or leave primary_city blank."
                )
            })

    def __str__(self):
        city_str = self.cities_str
        return f"{self.name} ({self.get_agent_type_display()}) — {city_str}"
