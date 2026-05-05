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
        ESCROW_TOKEN    = 'escrow_token',    'Escrow Token'
        VERIFICATION_FEE = 'verification_fee', 'Verification Fee'
        REPORT_FEE      = 'report_fee',      'Report Fee'

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
    amount_pkr  = models.BigIntegerField()
    purpose     = models.CharField(max_length=30, choices=Purpose.choices)
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reference   = models.CharField(max_length=200, blank=True)  # Bank/payment gateway ref
    metadata    = models.JSONField(default=dict, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payments'
        ordering = ['-created_at']

    def __str__(self):
        return f"PKR {self.amount_pkr:,} — {self.purpose} ({self.status})"