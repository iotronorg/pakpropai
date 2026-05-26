import uuid
from django.db import models
from django.conf import settings


class Campaign(models.Model):

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        SCHEDULED = 'scheduled', 'Scheduled'
        SENDING   = 'sending',   'Sending'
        SENT      = 'sent',      'Sent'
        CANCELLED = 'cancelled', 'Cancelled'
        FAILED    = 'failed',    'Failed'

    class AudienceFilter(models.TextChoices):
        ALL       = 'all',       'All Leads'
        NEW       = 'new',       'New Leads'
        WARM      = 'warm',      'Warm Leads'
        QUALIFIED = 'qualified', 'Qualified Leads'
        COLD      = 'cold',      'Cold Leads'
        BUY       = 'buy',       'Buy Intent'
        SELL      = 'sell',      'Sell Intent'
        RENT      = 'rent',      'Rent Intent'
        INVEST    = 'invest',    'Investment Intent'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization     = models.ForeignKey(
                           'organizations.Organization',
                           on_delete=models.CASCADE,
                           related_name='campaigns',
                       )
    created_by       = models.ForeignKey(
                           settings.AUTH_USER_MODEL,
                           on_delete=models.SET_NULL,
                           null=True,
                           related_name='created_campaigns',
                       )
    name             = models.CharField(max_length=200)
    message_template = models.TextField(max_length=4096, blank=True, default='')
    audience_filter  = models.CharField(
                           max_length=20,
                           choices=AudienceFilter.choices,
                           default=AudienceFilter.ALL,
                       )
    # Meta template fields (for outbound sends outside the 24h window)
    meta_template_name       = models.CharField(max_length=100, null=True, blank=True)
    meta_template_language   = models.CharField(max_length=10, default='en_US')
    meta_template_components = models.JSONField(default=list, blank=True)
    messaging_tier           = models.PositiveSmallIntegerField(
                                   default=1,
                                   help_text='Meta Business messaging tier: 1=1k/day, 2=10k/day, 3=100k/day',
                               )
    # Cohort filters (applied in addition to audience_filter)
    budget_min    = models.BigIntegerField(null=True, blank=True,
                        help_text='Include leads whose budget_max >= this value')
    budget_max    = models.BigIntegerField(null=True, blank=True,
                        help_text='Include leads whose budget_min <= this value')
    area_interest = models.CharField(max_length=100, null=True, blank=True,
                        help_text='Substring match on lead.city_interest')

    scheduled_at     = models.DateTimeField(null=True, blank=True)
    status           = models.CharField(
                           max_length=20,
                           choices=Status.choices,
                           default=Status.DRAFT,
                           db_index=True,
                       )
    recipient_count  = models.PositiveIntegerField(default=0)
    sent_count       = models.PositiveIntegerField(default=0)
    failed_count     = models.PositiveIntegerField(default=0)
    sent_at          = models.DateTimeField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'campaigns'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['status', 'scheduled_at']),
        ]

    def __str__(self):
        return f"{self.name} [{self.status}] — {self.organization.name}"


class CampaignRecipient(models.Model):

    class DeliveryStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT    = 'sent',    'Sent'
        FAILED  = 'failed',  'Failed'
        SKIPPED = 'skipped', 'Skipped'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign         = models.ForeignKey(Campaign, on_delete=models.CASCADE,
                           related_name='recipients')
    lead             = models.ForeignKey('leads.Lead', on_delete=models.CASCADE,
                           related_name='campaign_recipients')
    phone            = models.CharField(max_length=20)
    delivery_status  = models.CharField(
                           max_length=10,
                           choices=DeliveryStatus.choices,
                           default=DeliveryStatus.PENDING,
                       )
    sent_at          = models.DateTimeField(null=True, blank=True)
    error_message    = models.CharField(max_length=500, null=True, blank=True)
    meta_message_id  = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table        = 'campaign_recipients'
        unique_together = [['campaign', 'lead']]
        indexes         = [
            models.Index(fields=['campaign', 'delivery_status']),
        ]

    def __str__(self):
        return f"{self.campaign.name} → {self.phone} [{self.delivery_status}]"
