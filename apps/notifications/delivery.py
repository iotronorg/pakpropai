"""
Outbox handlers and enqueue helpers for user-facing deliveries (ADR-014).

The broker never sees an OTP code or a message body: the payload lives encrypted
in ``OutboxMessage`` and only the row id is queued (resolves KI-029).
"""
from __future__ import annotations

import logging
import uuid

from django.utils import timezone

from apps.core import outbox
from apps.core.outbox import PermanentDeliveryError

logger = logging.getLogger(__name__)


def _wa_message_id(resp) -> str:
    try:
        return (resp or {}).get('messages', [{}])[0].get('id', '') or ''
    except (AttributeError, IndexError, TypeError):
        return ''


# ── OTP ───────────────────────────────────────────────────────────────────────

def enqueue_otp(otp):
    """Queue delivery of a freshly issued OTP (``otp.code`` is the in-memory plaintext)."""
    return outbox.enqueue(
        'whatsapp.otp',
        {'phone': otp.phone, 'code': otp.code},
        key=f'otp:{otp.pk}',
        expires_at=otp.expires_at,
        max_attempts=4,
    )


def deliver_otp(msg, payload) -> str:
    from apps.whatsapp.client import get_wa_client
    phone, code = payload.get('phone'), payload.get('code')
    if not phone or not code:
        raise PermanentDeliveryError('payload missing phone/code')
    return _wa_message_id(get_wa_client(None).send_otp(phone, code))


# ── Notifications ─────────────────────────────────────────────────────────────

def enqueue_notification(notification, *, retry: bool = False):
    """
    Queue WhatsApp delivery of a Notification. A manual/periodic retry gets a fresh
    key; the handler still refuses to resend a notification already marked sent.
    """
    suffix = f':retry:{uuid.uuid4().hex[:12]}' if retry else ''
    return outbox.enqueue(
        'notification.whatsapp',
        {'notification_id': str(notification.pk)},
        key=f'notification:{notification.pk}{suffix}',
    )


def deliver_notification(msg, payload) -> str:
    from apps.notifications.models import Notification
    from apps.whatsapp.client import get_wa_client

    try:
        n = Notification.objects.select_related('user').get(pk=payload.get('notification_id'))
    except (Notification.DoesNotExist, ValueError):
        raise PermanentDeliveryError('notification not found')

    if n.status in (Notification.Status.SENT, Notification.Status.DELIVERED):
        return n.wa_message_id  # idempotent: already delivered by an earlier attempt

    try:
        resp = get_wa_client(None).send_text(n.user.phone, n.message)
    except ValueError as exc:
        # Outside the 24h customer-service window: retrying cannot help.
        n.status, n.error = Notification.Status.FAILED, str(exc)[:1000]
        n.save(update_fields=['status', 'error'])
        raise PermanentDeliveryError(str(exc)) from exc
    except Exception as exc:
        final = msg.attempts >= msg.max_attempts
        n.error = str(exc)[:1000]
        if final:
            n.status = Notification.Status.FAILED
        n.save(update_fields=['status', 'error'] if final else ['error'])
        raise

    n.status = Notification.Status.SENT
    n.wa_message_id = _wa_message_id(resp)
    n.sent_at = timezone.now()
    n.error = ''
    n.save(update_fields=['status', 'wa_message_id', 'sent_at', 'error'])
    return n.wa_message_id


# ── Free-form WhatsApp text (T-1022a) ────────────────────────────────────────

def enqueue_whatsapp_text(phone: str, text: str, *, key: str, organization=None,
                          skip_window_check: bool = False, expires_at=None):
    """
    Queue a free-form WhatsApp text through the outbox, in the caller's transaction.

    ``key`` is the per-site idempotency key: name the business event, e.g.
    ``hot_lead:<lead>:<last_scored_at>``, so a retried task or a re-fired signal sends
    nothing twice. ``organization`` selects the org's WhatsApp credentials at
    delivery time (global credentials when ``None``). Returns the ``OutboxMessage``,
    or ``None`` when there is no recipient.
    """
    phone = (phone or '').strip()
    if not phone or not text:
        return None
    return outbox.enqueue(
        'whatsapp.text',
        {'phone': phone, 'text': text, 'skip_window_check': bool(skip_window_check)},
        key=key,
        organization_id=getattr(organization, 'pk', None),
        expires_at=expires_at,
    )


def deliver_whatsapp_text(msg, payload) -> str:
    from apps.organizations.models import Organization
    from apps.whatsapp.client import get_wa_client

    phone, text = payload.get('phone'), payload.get('text')
    if not phone or not text:
        raise PermanentDeliveryError('payload missing phone/text')
    org = (Organization.objects.filter(pk=msg.organization_id).first()
           if msg.organization_id else None)
    try:
        resp = get_wa_client(org).send_text(
            phone, text, skip_window_check=bool(payload.get('skip_window_check')))
    except ValueError as exc:  # outside the 24h customer-service window
        raise PermanentDeliveryError(str(exc)) from exc
    return _wa_message_id(resp)
