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
        FAILED     = 'failed',     'Failed'

    class RequestSource(models.TextChoices):
        MANUAL    = 'manual',    'Manual'
        API       = 'api',       'API'
        AUTOMATED = 'automated', 'Automated'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                         related_name='deletion_requests')
    status         = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    requested_at   = models.DateTimeField(auto_now_add=True)
    completed_at   = models.DateTimeField(null=True, blank=True)
    regulation     = models.CharField(max_length=30, blank=True, null=True)
    erasure_scope  = models.JSONField(null=True, blank=True)
    requested_by   = models.ForeignKey(
                         settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                         null=True, blank=True, related_name='initiated_deletions')
    request_source = models.CharField(
                         max_length=20, choices=RequestSource.choices,
                         default=RequestSource.MANUAL)

    class Meta:
        db_table = 'compliance_data_deletion_requests'

    def __str__(self):
        return f"Deletion({self.user.phone}) — {self.status}"


class PIIDetectionEvent(models.Model):

    class Source(models.TextChoices):
        WHATSAPP_WEBHOOK = 'whatsapp_webhook', 'WhatsApp Webhook'
        API_SUBMISSION   = 'api_submission',   'API Submission'
        AI_RESPONSE      = 'ai_response',      'AI Response'
        LOG_WRITE        = 'log_write',        'Log Write'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org          = models.ForeignKey(
                       'organizations.Organization', on_delete=models.SET_NULL,
                       null=True, blank=True, related_name='pii_detection_events')
    source       = models.CharField(max_length=30, choices=Source.choices)
    field_path   = models.CharField(max_length=255, blank=True)
    pattern_name = models.CharField(max_length=30)
    masked_value = models.CharField(max_length=100)
    detected_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'compliance_pii_detection_events'

    def __str__(self):
        return f"PII({self.pattern_name}) @ {self.field_path} — {self.detected_at:%Y-%m-%d %H:%M}"


class ComplianceSanctionRecord(models.Model):

    class IDType(models.TextChoices):
        CNIC        = 'cnic',        'CNIC'
        PASSPORT    = 'passport',    'Passport'
        COMPANY_REG = 'company_reg', 'Company Registration'

    class ListSource(models.TextChoices):
        OFAC    = 'OFAC',    'OFAC'
        UN      = 'UN',      'UN Security Council'
        EU      = 'EU',      'EU Sanctions'
        FBR_CBR = 'FBR_CBR', 'FBR/CBR Pakistan'
        LOCAL   = 'LOCAL',   'Local Blacklist'

    class RiskLevel(models.TextChoices):
        HIGH   = 'high',   'High'
        MEDIUM = 'medium', 'Medium'
        LOW    = 'low',    'Low'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name             = models.CharField(max_length=200)
    id_number_prefix = models.CharField(max_length=4, blank=True)
    id_number_hash   = models.CharField(max_length=64, blank=True)
    id_type          = models.CharField(max_length=20, choices=IDType.choices, blank=True)
    list_source      = models.CharField(max_length=20, choices=ListSource.choices)
    risk_level       = models.CharField(max_length=10, choices=RiskLevel.choices, default=RiskLevel.HIGH)
    org              = models.ForeignKey(
                           'organizations.Organization', on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='sanction_records',
                           help_text='null = platform-level record matching all orgs')
    is_active        = models.BooleanField(default=True)
    added_by         = models.ForeignKey(
                           settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='added_sanctions')
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'compliance_sanction_records'

    def __str__(self):
        return f"Sanction({self.list_source}): {self.name}"


class SanctionScreeningResult(models.Model):

    class MatchType(models.TextChoices):
        EXACT    = 'exact',    'Exact Match'
        FUZZY    = 'fuzzy',    'Fuzzy Match'
        ID_MATCH = 'id_match', 'ID Match'

    class ScreeningStatus(models.TextChoices):
        CLEAR   = 'clear',   'Clear'
        FLAGGED = 'flagged', 'Flagged'
        BLOCKED = 'blocked', 'Blocked'

    screening_id     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    screened_name    = models.CharField(max_length=200)
    id_number_prefix = models.CharField(max_length=4, blank=True)
    id_number_hash   = models.CharField(max_length=64, blank=True)
    list_source      = models.CharField(max_length=20, blank=True)
    match_type       = models.CharField(max_length=20, choices=MatchType.choices, blank=True)
    risk_score       = models.PositiveSmallIntegerField(default=0)
    deal_lock        = models.ForeignKey(
                           'escrow.EscrowDeal', on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='screening_results')
    org              = models.ForeignKey(
                           'organizations.Organization', on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='screening_results')
    status           = models.CharField(
                           max_length=20, choices=ScreeningStatus.choices,
                           default=ScreeningStatus.CLEAR)
    screened_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'compliance_screening_results'
        indexes  = [models.Index(fields=['org', 'screened_at'])]

    def __str__(self):
        return f"Screening({self.status}): {self.screened_name} @ {self.screened_at:%Y-%m-%d %H:%M}"


class PrivacyAuditLog(models.Model):

    class Action(models.TextChoices):
        PII_DETECTED          = 'pii_detected',          'PII Detected'
        ERASURE_INITIATED     = 'erasure_initiated',     'Erasure Initiated'
        ERASURE_COMPLETED     = 'erasure_completed',     'Erasure Completed'
        CONSENT_CHANGE        = 'consent_change',        'Consent Change'
        EXPORT_GENERATED      = 'export_generated',      'Export Generated'
        DATA_TRANSFER_CHECKED = 'data_transfer_checked', 'Data Transfer Checked'

    id                 = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org                = models.ForeignKey(
                             'organizations.Organization', on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='privacy_audit_logs')
    action             = models.CharField(max_length=30, choices=Action.choices)
    actor              = models.ForeignKey(
                             settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='privacy_audit_actions')
    subject_identifier = models.CharField(max_length=64, blank=True)
    jurisdiction       = models.CharField(max_length=20, blank=True)
    regulation         = models.CharField(max_length=30, blank=True)
    details            = models.JSONField(default=dict, blank=True)
    is_sensitive       = models.BooleanField(default=False)
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'compliance_privacy_audit_logs'

    def __str__(self):
        return f"PrivacyAudit({self.action}) — {self.org} — {self.created_at:%Y-%m-%d %H:%M}"
