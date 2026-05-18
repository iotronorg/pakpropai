import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

LOCK_DURATION_HOURS = 48


class EscrowDeal(models.Model):

    class Status(models.TextChoices):
        INITIATED = 'initiated', 'Initiated'
        LOCKED    = 'locked',    'Locked'
        RELEASED  = 'released',  'Released'
        CANCELLED = 'cancelled', 'Cancelled'
        DISPUTED  = 'disputed',  'Disputed'
        EXPIRED   = 'expired',   'Expired'

    class Gateway(models.TextChoices):
        JAZZCASH  = 'jazzcash',  'JazzCash'
        EASYPAISA = 'easypaisa', 'EasyPaisa'
        BANK      = 'bank',      'Bank Transfer'
        SAFEPAY   = 'safepay',   'Safepay'
        MANUAL    = 'manual',    'Manual (Admin)'

    class Channel(models.TextChoices):
        WHATSAPP  = 'whatsapp',  'WhatsApp'
        DASHBOARD = 'dashboard', 'Dashboard'

    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(
                   'properties.Property',
                   on_delete=models.PROTECT,
                   related_name='escrow_deals'
               )
    buyer    = models.ForeignKey(
                   settings.AUTH_USER_MODEL,
                   on_delete=models.PROTECT,
                   related_name='escrow_as_buyer'
               )
    seller   = models.ForeignKey(
                   settings.AUTH_USER_MODEL,
                   on_delete=models.PROTECT,
                   null=True, blank=True,
                   related_name='escrow_as_seller'
               )
    agent    = models.ForeignKey(
                   'agents.Agent',
                   on_delete=models.SET_NULL,
                   null=True, blank=True,
                   related_name='deal_locks',
                   help_text='Agent who facilitated this deal lock'
               )
    token_amount = models.BigIntegerField(help_text='Token amount in the currency specified by currency field')
    currency     = models.CharField(
                       max_length=3, default='PKR',
                       help_text='ISO 4217 currency code for token_amount, e.g. PKR, AED, USD',
                   )
    status       = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    payment_gateway = models.CharField(max_length=20, choices=Gateway.choices, default=Gateway.MANUAL, blank=True)
    payment_ref     = models.CharField(max_length=200, blank=True, help_text='Gateway transaction ID or bank ref')
    initiated_via   = models.CharField(max_length=20, choices=Channel.choices, default=Channel.WHATSAPP)
    buyer_confirmed          = models.BooleanField(default=False)
    seller_confirmed         = models.BooleanField(default=False)
    seller_confirmation_token = models.CharField(max_length=8, blank=True)
    lock_started_at = models.DateTimeField(null=True, blank=True, help_text='When admin confirmed payment')
    lock_expires_at = models.DateTimeField(null=True, blank=True, help_text='lock_started_at + 48h')
    admin_notes     = models.TextField(blank=True)
    metadata        = models.JSONField(default=dict, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'escrow_deals'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['status']),
            models.Index(fields=['property']),
            models.Index(fields=['buyer']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['property'],
                condition=models.Q(status__in=['initiated', 'locked']),
                name='unique_active_deal_lock_per_property',
            )
        ]

    def activate_lock(self):
        """Confirm payment and start the 48h exclusivity window."""
        now = timezone.now()
        self.status          = self.Status.LOCKED
        self.buyer_confirmed = True
        self.lock_started_at = now
        self.lock_expires_at = now + timedelta(hours=LOCK_DURATION_HOURS)
        self.save(update_fields=['status', 'buyer_confirmed', 'lock_started_at', 'lock_expires_at', 'updated_at'])

    def is_active(self) -> bool:
        return self.status == self.Status.LOCKED and (
            self.lock_expires_at is None or self.lock_expires_at > timezone.now()
        )

    def hours_remaining(self):
        if self.lock_expires_at and self.status == self.Status.LOCKED:
            delta = self.lock_expires_at - timezone.now()
            return max(0.0, delta.total_seconds() / 3600)
        return None

    def __str__(self):
        return f"Deal Lock [{self.status}] — {self.property.title} ({self.currency} {self.token_amount:,})"