import uuid
from django.db import models
from django.conf import settings


class Verification(models.Model):

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        PASSED   = 'passed',   'Passed'
        FAILED   = 'failed',   'Failed'
        DISPUTED = 'disputed', 'Disputed'

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property      = models.ForeignKey(
                        'properties.Property',
                        on_delete=models.CASCADE,
                        related_name='verifications'
                    )
    requested_by  = models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True,
                        related_name='verification_requests'
                    )
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    ocr_data      = models.JSONField(default=dict, blank=True)
    fraud_flags   = models.JSONField(default=list, blank=True)
    notes         = models.TextField(blank=True)
    verified_at   = models.DateTimeField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'verifications'
        ordering = ['-created_at']

    def __str__(self):
        return f"Verification {self.status} — {self.property.title}"