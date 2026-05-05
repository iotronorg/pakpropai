
import uuid
from django.db import models
from django.conf import settings


class Report(models.Model):

    class ReportType(models.TextChoices):
        PROPERTY_ANALYSIS = 'property_analysis', 'Property Analysis'
        TAX_ADVISORY      = 'tax_advisory',      'Tax Advisory'
        LOAN_ELIGIBILITY  = 'loan_eligibility',  'Loan Eligibility'
        FRAUD_CHECK       = 'fraud_check',       'Fraud Check'

    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        GENERATING = 'generating', 'Generating'
        READY      = 'ready',      'Ready'
        FAILED     = 'failed',     'Failed'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user        = models.ForeignKey(
                      settings.AUTH_USER_MODEL,
                      on_delete=models.CASCADE,
                      related_name='reports'
                  )
    property    = models.ForeignKey(
                      'properties.Property',
                      on_delete=models.SET_NULL,
                      null=True, blank=True,
                      related_name='reports'
                  )
    report_type = models.CharField(max_length=30, choices=ReportType.choices)
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    content     = models.JSONField(default=dict, blank=True)   # AI-generated content
    file_url    = models.URLField(blank=True)                  # R2 PDF URL
    created_at  = models.DateTimeField(auto_now_add=True)
    ready_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'reports'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.report_type} — {self.user.phone} ({self.status})"