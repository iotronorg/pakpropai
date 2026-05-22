import uuid
from django.db import models
from django.conf import settings


class ConsentRecord(models.Model):

    class Purpose(models.TextChoices):
        MARKETING     = 'marketing',     'Marketing Communications'
        ANALYTICS     = 'analytics',     'Analytics & Product Improvement'
        AI_PROCESSING = 'ai_processing', 'AI Conversation Processing'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                     related_name='consent_records')
    purpose    = models.CharField(max_length=30, choices=Purpose.choices)
    given_at   = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    is_active  = models.BooleanField(default=True)

    class Meta:
        db_table        = 'compliance_consent_records'
        unique_together = [('user', 'purpose')]

    def __str__(self):
        return f"{self.user.phone} — {self.purpose} ({'active' if self.is_active else 'revoked'})"


class DataExportRequest(models.Model):

    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        PROCESSING = 'processing', 'Processing'
        READY      = 'ready',      'Ready for Download'
        EXPIRED    = 'expired',    'Expired'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                       related_name='export_requests')
    status       = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    file_url     = models.CharField(max_length=500, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'compliance_data_export_requests'

    def __str__(self):
        return f"Export({self.user.phone}) — {self.status}"


class DataDeletionRequest(models.Model):

    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED  = 'completed',  'Completed'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                       related_name='deletion_requests')
    status       = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'compliance_data_deletion_requests'

    def __str__(self):
        return f"Deletion({self.user.phone}) — {self.status}"
