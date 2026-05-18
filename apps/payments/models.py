import uuid
from django.db import models
from django.conf import settings


class Payment(models.Model):

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED    = 'failed',    'Failed'
        REFUNDED  = 'refunded',  'Refunded'

    class Purpose(models.TextChoices):
        ESCROW_TOKEN     = 'escrow_token',     'Escrow Token'
        VERIFICATION_FEE = 'verification_fee', 'Verification Fee'
        REPORT_FEE       = 'report_fee',       'Report Fee'

    class Gateway(models.TextChoices):
        SAFEPAY   = 'safepay',   'Safepay'
        BSECURE   = 'bsecure',   'bSecure'
        JAZZCASH  = 'jazzcash',  'JazzCash'
        EASYPAISA = 'easypaisa', 'EasyPaisa'
        MANUAL    = 'manual',    'Manual'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user        = models.ForeignKey(
                      settings.AUTH_USER_MODEL,
                      on_delete=models.PROTECT,
                      related_name='payments'
                  )
    escrow_deal = models.ForeignKey(
                      'escrow.EscrowDeal',
                      on_delete=models.SET_NULL,
                      null=True, blank=True,
                      related_name='payments'
                  )
    amount   = models.BigIntegerField(
                   help_text='Amount in the currency specified by the currency field',
               )
    currency = models.CharField(
                   max_length=3, default='PKR',
                   help_text='ISO 4217 currency code for amount, e.g. PKR, AED, USD',
               )
    purpose  = models.CharField(max_length=30, choices=Purpose.choices)
    gateway       = models.CharField(max_length=20, choices=Gateway.choices, default=Gateway.MANUAL)
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reference     = models.CharField(max_length=200, blank=True, help_text='Gateway transaction ID or bank ref')
    checkout_token = models.CharField(max_length=500, blank=True, help_text='Gateway checkout session token')
    checkout_url   = models.URLField(max_length=1000, blank=True, help_text='Redirect URL for online payment')
    webhook_payload = models.JSONField(default=dict, blank=True, help_text='Raw webhook payload for audit')
    metadata      = models.JSONField(default=dict, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payments'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['status']),
            models.Index(fields=['checkout_token']),
        ]

    def __str__(self):
        return f"{self.currency} {self.amount:,} — {self.purpose} [{self.gateway}] ({self.status})"