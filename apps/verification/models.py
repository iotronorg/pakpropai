import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone


class DocumentScan(models.Model):
    """Stores OCR results from documents sent via WhatsApp — no property FK required."""

    class DocType(models.TextChoices):
        # Pakistan
        FARD       = 'fard',       'Fard (Ownership Record)'
        ALLOTMENT  = 'allotment',  'Allotment Letter'
        SALE_DEED  = 'sale_deed',  'Sale Deed / Registry'
        NOC        = 'noc',        'No Objection Certificate'
        TAX_CERT   = 'tax_cert',   'Tax Certificate'
        CNIC       = 'cnic',       'CNIC'
        POA        = 'poa',        'Power of Attorney'
        # UAE
        TITLE_DEED  = 'title_deed',  'Title Deed (DLD)'
        OQOOD       = 'oqood',       'Oqood (Off-plan Registration)'
        EMIRATES_ID = 'emirates_id', 'Emirates ID'
        # UK
        TITLE_REGISTER   = 'title_register',   'Title Register (HM Land Registry)'
        LAND_CERTIFICATE = 'land_certificate',  'Land Certificate'
        MORTGAGE_DEED    = 'mortgage_deed',     'Mortgage Deed'
        # US
        WARRANTY_DEED      = 'warranty_deed',      'Warranty Deed'
        TITLE_INSURANCE    = 'title_insurance',    'Title Insurance Policy'
        HOA_DOCS           = 'hoa_docs',           'HOA Documents'
        CLOSING_DISCLOSURE = 'closing_disclosure', 'Closing Disclosure (HUD-1)'
        PROMISSORY_NOTE    = 'promissory_note',    'Promissory Note / Mortgage Note'
        DRIVERS_LICENSE    = 'drivers_license',    "Driver's License"
        STATE_ID           = 'state_id',           'State ID'
        # Global
        PASSPORT        = 'passport',        'Passport'
        DRIVING_LICENCE = 'driving_licence', 'Driving Licence'
        OTHER           = 'other',           'Other Document'

    class Status(models.TextChoices):
        CLEAN       = 'clean',       'Clean'
        SUSPICIOUS  = 'suspicious',  'Suspicious'
        UNREADABLE  = 'unreadable',  'Unreadable'

    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='document_scans')
    verification  = models.ForeignKey(
                        'Verification', on_delete=models.SET_NULL,
                        null=True, blank=True, related_name='document_scans'
                    )
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
    reviewer      = models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True, blank=True,
                        related_name='reviewed_verifications'
                    )
    signal_score  = models.SmallIntegerField(null=True, blank=True)
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


class FraudBlacklist(models.Model):
    """
    Persistent fraud blacklist.
    On save/delete the token is synced to the Redis cache so FraudCheckService
    fast-path checks stay consistent.
    """
    token      = models.CharField(max_length=200, unique=True,
                     help_text='Keyword, phone, CNIC, society name, or any fraud signal')
    reason     = models.TextField(blank=True, help_text='Why this token was blacklisted')
    added_by   = models.ForeignKey(
                     settings.AUTH_USER_MODEL,
                     on_delete=models.SET_NULL,
                     null=True, blank=True,
                     related_name='blacklist_entries',
                 )
    expires_at = models.DateTimeField(null=True, blank=True,
                     help_text='Auto-expiry date. Null = never expires.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'fraud_blacklist'
        ordering = ['-created_at']

    def is_active(self) -> bool:
        return self.expires_at is None or self.expires_at > timezone.now()

    def sync_to_cache(self):
        from django.core.cache import cache
        if self.is_active():
            ttl = None
            if self.expires_at:
                ttl = int((self.expires_at - timezone.now()).total_seconds())
            cache.set(f'fraud:blacklist:{self.token.lower()}', True, ttl)
        else:
            cache.delete(f'fraud:blacklist:{self.token.lower()}')

    def remove_from_cache(self):
        from django.core.cache import cache
        cache.delete(f'fraud:blacklist:{self.token.lower()}')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.sync_to_cache()

    def delete(self, *args, **kwargs):
        self.remove_from_cache()
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"Blacklist: {self.token}"