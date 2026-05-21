import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone


class OrgSubscription(models.Model):

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Active'
        PAST_DUE  = 'past_due',  'Past Due'
        CANCELLED = 'cancelled', 'Cancelled'
        TRIALING  = 'trialing',  'Trialing'
        INCOMPLETE = 'incomplete', 'Incomplete'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    stripe_customer_id    = models.CharField(max_length=200, blank=True, db_index=True)
    stripe_subscription_id = models.CharField(max_length=200, blank=True, db_index=True)
    plan                  = models.CharField(max_length=30, default='trial')
    status                = models.CharField(
        max_length=20, choices=Status.choices, default=Status.TRIALING
    )
    current_period_end    = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end  = models.BooleanField(default=False)
    created_at            = models.DateTimeField(auto_now_add=True)
    updated_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_subscriptions'

    def __str__(self):
        return f"{self.organization.name} — {self.plan} ({self.status})"


class WebhookEvent(models.Model):
    """
    Idempotency guard for inbound webhooks from payment gateways.
    Each (gateway, event_id) pair is processed at most once.
    """
    gateway      = models.CharField(max_length=30)
    event_id     = models.CharField(max_length=500)
    processed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table         = 'billing_webhook_events'
        unique_together  = [('gateway', 'event_id')]
        indexes          = [models.Index(fields=['gateway', 'event_id'])]

    def __str__(self):
        return f"{self.gateway}:{self.event_id}"
