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
    message_template = models.TextField(max_length=4096)
    audience_filter  = models.CharField(
                           max_length=20,
                           choices=AudienceFilter.choices,
                           default=AudienceFilter.ALL,
                       )
    scheduled_at     = models.DateTimeField(
                           null=True, blank=True,
                           help_text='If set, campaign sends at this time. If null, send immediately on trigger.',
                       )
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
