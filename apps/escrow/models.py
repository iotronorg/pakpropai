import uuid
from django.db import models
from django.conf import settings


class EscrowDeal(models.Model):

    class Status(models.TextChoices):
        INITIATED = 'initiated', 'Initiated'
        LOCKED    = 'locked',    'Locked'
        RELEASED  = 'released',  'Released'
        CANCELLED = 'cancelled', 'Cancelled'
        DISPUTED  = 'disputed',  'Disputed'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property        = models.ForeignKey(
                          'properties.Property',
                          on_delete=models.PROTECT,
                          related_name='escrow_deals'
                      )
    buyer           = models.ForeignKey(
                          settings.AUTH_USER_MODEL,
                          on_delete=models.PROTECT,
                          related_name='escrow_as_buyer'
                      )
    seller          = models.ForeignKey(
                          settings.AUTH_USER_MODEL,
                          on_delete=models.PROTECT,
                          related_name='escrow_as_seller'
                      )
    token_amount    = models.BigIntegerField()           # PKR, stored as integer — no floats for money
    status          = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    buyer_confirmed  = models.BooleanField(default=False)
    seller_confirmed = models.BooleanField(default=False)
    lock_expires_at = models.DateTimeField(null=True, blank=True)
    metadata        = models.JSONField(default=dict, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'escrow_deals'
        ordering = ['-created_at']

    def __str__(self):
        return f"Escrow {self.status} — {self.property.title} (PKR {self.token_amount:,})"