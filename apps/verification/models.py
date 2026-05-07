import uuid
from django.db import models
from django.conf import settings


class DocumentScan(models.Model):
    """Stores OCR results from documents sent via WhatsApp — no property FK required."""

    class DocType(models.TextChoices):
        FARD       = 'fard',       'Fard (Ownership Record)'
        ALLOTMENT  = 'allotment',  'Allotment Letter'
        SALE_DEED  = 'sale_deed',  'Sale Deed / Registry'
        NOC        = 'noc',        'No Objection Certificate'
        TAX_CERT   = 'tax_cert',   'Tax Certificate'
        CNIC       = 'cnic',       'CNIC'
        POA        = 'poa',        'Power of Attorney'
        OTHER      = 'other',      'Other Document'

    class Status(models.TextChoices):
        CLEAN       = 'clean',       'Clean'
        SUSPICIOUS  = 'suspicious',  'Suspicious'
        UNREADABLE  = 'unreadable',  'Unreadable'

    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='document_scans')
    phone         = models.CharField(max_length=20, blank=True)
    document_type = models.CharField(max_length=20, choices=DocType.choices, default=DocType.OTHER)

    # Extracted fields
    owner_name          = models.CharField(max_length=200, blank=True)
    cnic_number         = models.CharField(max_length=20,  blank=True)
    property_address    = models.TextField(blank=True)
    area                = models.CharField(max_length=100, blank=True)
    registration_number = models.CharField(max_length=100, blank=True)
    issue_date          = models.CharField(max_length=50,  blank=True)
    authority           = models.CharField(max_length=200, blank=True)

    # Analysis
    extracted_fields  = models.JSONField(default=dict)   # all key-value pairs from OCR
    red_flags         = models.JSONField(default=list)   # suspicious items found
    confidence        = models.CharField(max_length=10, default='LOW')  # HIGH/MEDIUM/LOW
    raw_ocr           = models.TextField(blank=True)
    whatsapp_summary  = models.TextField(blank=True)
    status            = models.CharField(max_length=20, choices=Status.choices,
                                         default=Status.UNREADABLE)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.owner_name or self.phone} [{self.status}]"


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