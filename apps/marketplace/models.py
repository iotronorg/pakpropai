import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q


class SyndicationListing(models.Model):

    class Status(models.TextChoices):
        DRAFT      = 'draft',      'Draft'
        SYNDICATED = 'syndicated', 'Syndicated'
        WITHDRAWN  = 'withdrawn',  'Withdrawn'

    class CommissionType(models.TextChoices):
        FIXED      = 'fixed',      'Fixed'
        PERCENTAGE = 'percentage', 'Percentage'

    class SyndicationScope(models.TextChoices):
        PLATFORM_WIDE      = 'platform_wide',      'Platform Wide'
        SELECTED_PARTNERS  = 'selected_partners',  'Selected Partners'

    id                 = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property           = models.ForeignKey('properties.Property', on_delete=models.PROTECT, related_name='syndication_listings')
    developer_org      = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='syndication_listings')
    status             = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    commission_type    = models.CharField(max_length=20, choices=CommissionType.choices)
    commission_value   = models.DecimalField(max_digits=12, decimal_places=4)
    commission_currency = models.CharField(max_length=3, default='USD')
    syndication_scope  = models.CharField(max_length=30, choices=SyndicationScope.choices, default=SyndicationScope.PLATFORM_WIDE)
    description        = models.TextField(blank=True)
    expires_at         = models.DateTimeField(null=True, blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)
    updated_at         = models.DateTimeField(auto_now=True)

    class Meta:
        app_label      = 'marketplace'
        db_table       = 'syndication_listings'
        unique_together = [('property', 'developer_org')]


class BrokerNetworkPartnership(models.Model):

    class Status(models.TextChoices):
        INVITED   = 'invited',   'Invited'
        ACTIVE    = 'active',    'Active'
        SUSPENDED = 'suspended', 'Suspended'
        REVOKED   = 'revoked',   'Revoked'

    class CommissionOverrideType(models.TextChoices):
        FIXED      = 'fixed',      'Fixed'
        PERCENTAGE = 'percentage', 'Percentage'

    id                       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    developer_org            = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='broker_partnerships_as_developer')
    broker_org               = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, null=True, blank=True, related_name='broker_partnerships_as_broker')
    broker_agent             = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='broker_partnerships')
    status                   = models.CharField(max_length=20, choices=Status.choices, default=Status.INVITED)
    commission_override_type = models.CharField(max_length=20, choices=CommissionOverrideType.choices, null=True, blank=True)
    commission_override_value = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    invited_at               = models.DateTimeField(auto_now_add=True)
    activated_at             = models.DateTimeField(null=True, blank=True)
    notes                    = models.TextField(blank=True)

    class Meta:
        app_label = 'marketplace'
        db_table  = 'broker_network_partnerships'
        constraints = [
            models.CheckConstraint(
                check=Q(broker_org__isnull=False) | Q(broker_agent__isnull=False),
                name='partnership_has_broker_party',
            )
        ]


class SyndicationLeadSubmission(models.Model):

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        ACCEPTED  = 'accepted',  'Accepted'
        REJECTED  = 'rejected',  'Rejected'
        CONVERTED = 'converted', 'Converted'

    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing             = models.ForeignKey(SyndicationListing, on_delete=models.CASCADE, related_name='lead_submissions')
    lead                = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='syndication_submissions')
    submitted_by_org    = models.ForeignKey('organizations.Organization', on_delete=models.SET_NULL, null=True, blank=True, related_name='submitted_syndication_leads')
    submitted_by_agent  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='submitted_syndication_leads')
    status              = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    commission_calculated = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    commission_currency = models.CharField(max_length=3, default='USD')
    deal_lock           = models.ForeignKey('escrow.EscrowDeal', on_delete=models.SET_NULL, null=True, blank=True, related_name='syndication_submissions')
    submitted_at        = models.DateTimeField(auto_now_add=True)
    reviewed_at         = models.DateTimeField(null=True, blank=True)
    reviewed_by         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_syndication_submissions')

    class Meta:
        app_label = 'marketplace'
        db_table  = 'syndication_lead_submissions'


class CommissionLedgerEntry(models.Model):

    class CommissionType(models.TextChoices):
        FIXED      = 'fixed',      'Fixed'
        PERCENTAGE = 'percentage', 'Percentage'

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        PAID      = 'paid',      'Paid'
        DISPUTED  = 'disputed',  'Disputed'

    entry_id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing           = models.ForeignKey(SyndicationListing, on_delete=models.PROTECT, related_name='ledger_entries')
    submission        = models.ForeignKey(SyndicationLeadSubmission, on_delete=models.PROTECT, related_name='ledger_entries')
    deal_lock         = models.ForeignKey('escrow.EscrowDeal', on_delete=models.SET_NULL, null=True, blank=True, related_name='commission_ledger_entries')
    developer_org     = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT, related_name='commission_entries_as_developer')
    broker_org        = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT, null=True, blank=True, related_name='commission_entries_as_broker')
    broker_agent      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='commission_entries_as_broker_agent')
    commission_amount  = models.DecimalField(max_digits=12, decimal_places=4)
    commission_currency = models.CharField(max_length=3, default='USD')
    commission_type   = models.CharField(max_length=20, choices=CommissionType.choices)
    source_chain_hash = models.CharField(max_length=64)
    prev_entry_hash   = models.CharField(max_length=64, blank=True)
    status            = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'marketplace'
        db_table  = 'commission_ledger_entries'

    def delete(self, *args, **kwargs):
        raise PermissionError('CommissionLedgerEntry is append-only and cannot be deleted')
