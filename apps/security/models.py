import hashlib
import hmac
import json
import uuid

from django.conf import settings
from django.db import models


_CHAIN_SECRET = None


def _get_chain_secret() -> bytes:
    global _CHAIN_SECRET
    if _CHAIN_SECRET is None:
        _CHAIN_SECRET = settings.SECRET_KEY.encode()
    return _CHAIN_SECRET


def _compute_record_hash(event_data: dict, prev_hash: str) -> str:
    canonical = json.dumps(event_data, sort_keys=True, default=str)
    payload = f"{canonical}|{prev_hash}".encode()
    return hmac.new(_get_chain_secret(), payload, hashlib.sha256).hexdigest()


class AppendOnlyManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset()


class ApiSecurityEvent(models.Model):

    GENESIS_HASH = '0' * 64

    class EventType(models.TextChoices):
        SQL_INJECTION       = 'sql_injection',       'SQL Injection'
        CROSS_TENANT        = 'cross_tenant',         'Cross-Tenant Access'
        JWT_ANOMALY         = 'jwt_anomaly',          'JWT Anomaly'
        AUTH_FAILURE        = 'auth_failure',         'Auth Failure'
        RATE_LIMIT_BLOCKED  = 'rate_limit_blocked',   'Rate-Limit Block'
        SUSPICIOUS_HEADER   = 'suspicious_header',    'Suspicious Header'
        PARAMETER_TAMPERING = 'parameter_tampering',  'Parameter Tampering'

    class Severity(models.IntegerChoices):
        LOW      = 1, 'Low'
        MEDIUM   = 2, 'Medium'
        HIGH     = 3, 'High'
        CRITICAL = 4, 'Critical'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type      = models.CharField(max_length=30, choices=EventType.choices, db_index=True)
    severity        = models.SmallIntegerField(choices=Severity.choices, default=Severity.HIGH)

    ip_address      = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_id         = models.CharField(max_length=100, blank=True, db_index=True)
    organization_id = models.CharField(max_length=100, blank=True, db_index=True)
    endpoint        = models.CharField(max_length=255, blank=True)
    http_method     = models.CharField(max_length=10, blank=True)

    threat_detail   = models.TextField(blank=True)
    request_id      = models.CharField(max_length=64, blank=True, db_index=True)

    prev_hash       = models.CharField(max_length=64, blank=True)
    record_hash     = models.CharField(max_length=64, blank=True, db_index=True)

    created_at      = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AppendOnlyManager()

    class Meta:
        db_table = 'api_security_events'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['event_type', '-created_at'], name='sec_evt_type_created_idx'),
            models.Index(fields=['ip_address',  '-created_at'], name='sec_ip_created_idx'),
            models.Index(fields=['organization_id', '-created_at'], name='sec_org_created_idx'),
        ]

    def _canonical_data(self) -> dict:
        return {
            'id':              str(self.id),
            'event_type':      self.event_type,
            'severity':        self.severity,
            'ip_address':      self.ip_address,
            'user_id':         self.user_id,
            'organization_id': self.organization_id,
            'endpoint':        self.endpoint,
            'http_method':     self.http_method,
            'threat_detail':   self.threat_detail,
        }

    def save(self, *args, **kwargs):
        if self.pk and ApiSecurityEvent.objects.filter(pk=self.pk).exists():
            raise PermissionError('ApiSecurityEvent records are append-only and cannot be updated.')
        if not self.prev_hash:
            last = ApiSecurityEvent.objects.order_by('-created_at').first()
            self.prev_hash = last.record_hash if last else self.GENESIS_HASH
        if not self.record_hash:
            self.record_hash = _compute_record_hash(self._canonical_data(), self.prev_hash)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError('ApiSecurityEvent records are immutable and cannot be deleted.')

    @classmethod
    def verify_chain(cls, limit: int = 1000) -> tuple:
        events = list(cls.objects.order_by('created_at')[:limit])
        if not events:
            return True, 'OK — empty log'
        prev = cls.GENESIS_HASH
        for evt in events:
            expected = _compute_record_hash(evt._canonical_data(), evt.prev_hash)
            if evt.record_hash != expected:
                return False, f'Chain broken at record {evt.id}: hash mismatch'
            if evt.prev_hash != prev:
                return False, f'Chain broken at record {evt.id}: prev_hash mismatch'
            prev = evt.record_hash
        return True, f'OK — {len(events)} records verified'

    def __str__(self):
        return f'[{self.get_severity_display()}] {self.event_type} — {self.ip_address}'
