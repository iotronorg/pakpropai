"""
The unified audit trail (T-3070, GAP-104, ADR-060).

* :func:`record` appends one ``AuditEvent``. Rows are chained: each stores the
  previous row's hash, and its own hash is an HMAC (key ``AUDIT_CHAIN_KEY``,
  default ``SECRET_KEY``) over its fixed fields, the digest of its personal
  part and that previous hash. A transaction-scoped advisory lock makes the
  chain strictly linear even with many writers.
* :func:`verify` walks the chain and reports the first row that does not fit.
* :func:`redact_person` empties the personal part of rows about someone who
  is erased; the chain still verifies because only the digest is chained.
* The three older logs (``core.AuditLog``, ``compliance.PrivacyAuditLog``,
  ``security.ApiSecurityEvent``) are mirrored here as they are written (expand
  phase); readers move to this trail and the old tables go later.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

GENESIS = '0' * 64
LOCK_KEY = 7_310_400_104          # pg_advisory_xact_lock key for the chain head
CATEGORIES = ('admin', 'organization', 'privacy', 'security', 'ai', 'listing')
_CODE = re.compile(r'^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$')


def _key() -> bytes:
    return (getattr(settings, 'AUDIT_CHAIN_KEY', '') or settings.SECRET_KEY).encode()


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, ensure_ascii=False)


def _plain(value):
    """What a JSON column gives back, so the digest is computed on the stored form."""
    return json.loads(_canonical(value or {}))


def digest(personal: dict) -> str:
    return hashlib.sha256(_canonical(personal or {}).encode()).hexdigest()


def row_hash(e) -> str:
    fields = {'seq': e.seq, 'at': e.at.isoformat(), 'category': e.category, 'action': e.action,
              'actor_id': e.actor_id, 'organization_id': e.organization_id, 'target_type': e.target_type,
              'target_id': e.target_id, 'source': e.source, 'meta': e.meta, 'personal_digest': e.personal_digest}
    return hmac.new(_key(), f'{_canonical(fields)}|{e.prev_hash}'.encode(), hashlib.sha256).hexdigest()


def _id(value) -> str:
    if value is None:
        return ''
    return str(getattr(value, 'pk', value))[:64]


def record(action: str, *, category: str = 'admin', actor=None, organization=None, target=None,
           target_type: str = '', target_id: str = '', meta: dict | None = None, personal: dict | None = None,
           source: str = ''):
    """Append one event. ``meta`` must hold no personal data; anything personal goes in ``personal``."""
    from .models import AuditEvent
    if category not in CATEGORIES:
        raise ValueError(f'unknown audit category {category!r}')
    if target is not None:
        target_type = target_type or type(target).__name__
        target_id = target_id or _id(target)
    personal = _plain(personal)
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s)', [LOCK_KEY])
        last = AuditEvent.objects.order_by('-seq').values('seq', 'record_hash').first()
        e = AuditEvent(seq=(last['seq'] + 1) if last else 1, at=timezone.now(), category=category,
                       action=action[:80], actor_id=_id(actor), organization_id=_id(organization),
                       target_type=target_type[:60], target_id=str(target_id)[:100], source=source[:40],
                       meta=_plain(meta), personal=personal, personal_digest=digest(personal),
                       prev_hash=last['record_hash'] if last else GENESIS)
        e.record_hash = row_hash(e)
        e.save(force_insert=True)
    return e


def safe_record(action: str, **kwargs):
    """For mirrors and side paths: a failed audit write is logged loudly, never raised."""
    try:
        return record(action, **kwargs)
    except Exception:  # noqa: BLE001
        logger.error('audit.write_failed action=%s', action, exc_info=True)
        try:
            from apps.core.metrics import audit_write_failures_total
            audit_write_failures_total.inc()
        except Exception:  # noqa: BLE001
            pass
        return None


# ── Verification ──────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    ok: bool
    checked: int
    head_seq: int | None
    head_hash: str
    broken_at: int | None = None
    reason: str = ''

    def as_dict(self) -> dict:
        return {'ok': self.ok, 'checked': self.checked, 'head_seq': self.head_seq, 'head_hash': self.head_hash,
                'broken_at': self.broken_at, 'reason': self.reason}


def verify(chunk: int = 2000) -> Verdict:
    """Walk the whole chain in order. Stops at the first row that does not fit."""
    from .models import AuditEvent
    prev_hash, expected_seq, checked, last = GENESIS, 1, 0, None
    for e in AuditEvent.objects.order_by('seq').iterator(chunk_size=chunk):
        problem = ''
        if e.seq != expected_seq:
            problem = f'missing rows before {e.seq}'
        elif e.prev_hash != prev_hash:
            problem = 'previous hash does not match'
        elif not hmac.compare_digest(e.record_hash, row_hash(e)):
            problem = 'row changed'
        elif e.personal and digest(e.personal) != e.personal_digest:
            problem = 'personal data changed'
        elif not e.personal and e.redacted_at is None and e.personal_digest != digest({}):
            problem = 'personal data removed without redaction'
        if problem:
            return Verdict(False, checked, last.seq if last else None, last.record_hash if last else GENESIS,
                           e.seq, problem)
        prev_hash, expected_seq, checked, last = e.record_hash, e.seq + 1, checked + 1, e
    return Verdict(True, checked, last.seq if last else None, last.record_hash if last else GENESIS)


# ── Erasure ───────────────────────────────────────────────────────────────────

def redact_person(user, phones=()) -> int:
    """Empty the personal part of every row by, about or mentioning the person."""
    from django.db.models import Q, TextField
    from django.db.models.functions import Cast

    from .models import AuditEvent
    uid = str(user.pk)
    mentions = [t for t in (*phones, getattr(user, 'email', '') or '') if t and len(t) >= 6]
    q = Q(actor_id=uid) | Q(target_type='User', target_id=uid)
    rows = AuditEvent.objects.exclude(personal={}).annotate(_text=Cast('personal', TextField()))
    for text in mentions:
        q |= Q(_text__contains=text) | Q(_text__contains=text.lstrip('+'))
    q |= Q(_text__contains=uid)
    return rows.filter(q).update(personal={}, redacted_at=timezone.now())


# ── Mirrors of the older logs (expand phase) ─────────────────────────────────

def _code(text: str, fallback: str) -> str:
    text = (text or '').strip()
    return text if _CODE.match(text) else fallback


def mirror_admin_log(row) -> None:
    """``core.AuditLog``: admin, membership and configuration actions."""
    action = _code(row.detail, f'{(row.target_model or "record").lower()}.{row.action}')
    category = ('organization' if action.split('.')[0] in ('membership', 'organization', 'invite')
                else 'ai' if row.target_model in ('AIKillSwitch',) else 'admin')
    safe_record(action, category=category, actor=row.actor_id, organization=getattr(row, '_organization_id', ''),
                target_type=row.target_model, target_id=row.target_id, source='core.AuditLog',
                meta={'legacy_id': row.pk, 'kind': row.action},
                personal={k: v for k, v in {'detail': row.detail, 'before': row.before, 'after': row.after,
                                             'ip': row.ip_address, 'user_agent': row.user_agent}.items() if v})


def mirror_privacy_log(row) -> None:
    """``compliance.PrivacyAuditLog``: erasure, export, consent. The subject is a keyed hash already."""
    safe_record(f'privacy.{row.action}', category='privacy', actor=row.actor_id, organization=row.org_id,
                target_type='subject', target_id=row.subject_identifier, source='compliance.PrivacyAuditLog',
                meta={'legacy_id': str(row.pk), 'jurisdiction': row.jurisdiction, 'regulation': row.regulation,
                      'sensitive': row.is_sensitive},
                personal={'details': row.details} if row.details else {})


def mirror_security_event(row) -> None:
    """``security.ApiSecurityEvent``: blocked requests, auth failures, cross-tenant attempts."""
    safe_record(f'security.{row.event_type}', category='security', actor=row.user_id,
                organization=row.organization_id, source='security.ApiSecurityEvent',
                meta={'legacy_id': str(row.pk), 'severity': int(row.severity)},
                personal={k: v for k, v in {'ip': row.ip_address, 'endpoint': row.endpoint, 'method': row.http_method,
                                             'detail': row.threat_detail, 'request_id': row.request_id}.items() if v})


def connect_mirrors() -> None:
    from django.db.models.signals import post_save

    from apps.compliance.models import PrivacyAuditLog
    from apps.security.models import ApiSecurityEvent

    from .models import AuditLog

    for model, fn in ((AuditLog, mirror_admin_log), (PrivacyAuditLog, mirror_privacy_log),
                      (ApiSecurityEvent, mirror_security_event)):
        def receiver(sender, instance, created, raw=False, _fn=fn, **kwargs):
            if created and not raw:
                _fn(instance)
        post_save.connect(receiver, sender=model, weak=False, dispatch_uid=f'audit-mirror-{model._meta.label}')
