"""
Transactional outbox for outbound side effects (ADR-014).

Write side::

    with transaction.atomic():
        ...business change...
        outbox.enqueue('whatsapp.otp', {'phone': p, 'code': c}, key=f'otp:{otp.pk}')

The row commits (or rolls back) with the business change. After commit the
dispatch task is queued with the row id only; ``relay_due`` (Celery beat) re-queues
anything whose on-commit enqueue was lost or whose worker died mid-send.

Delivery is **at-least-once**. A worker that crashes after the provider accepted
the message but before the row is marked sent will cause one duplicate after the
lease expires. Handlers that touch another record (e.g. a Notification) should make
themselves idempotent on that record's state.

Handler contract — ``handler(message, payload: dict) -> str | None``:
  * return the provider reference (or None) on success;
  * raise :class:`PermanentDeliveryError` when retrying cannot help;
  * any other exception is retried with exponential backoff up to ``max_attempts``.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

# kind → dotted path of the handler. Explicit so a worker never depends on import order.
HANDLERS: dict[str, str] = {
    'whatsapp.otp':          'apps.notifications.delivery.deliver_otp',
    'notification.whatsapp': 'apps.notifications.delivery.deliver_notification',
}
# Payload is wiped once the message reaches a terminal state (data minimisation).
SENSITIVE_KINDS = frozenset({'whatsapp.otp'})

LEASE = timedelta(minutes=2)
BACKOFF_BASE_SECONDS = 15
BACKOFF_MAX_SECONDS = 15 * 60


class PermanentDeliveryError(Exception):
    """Delivery cannot succeed by retrying (invalid recipient, closed 24h window…)."""


def _model():
    from apps.core.models import OutboxMessage
    return OutboxMessage


def queue_name() -> str:
    return getattr(settings, 'OUTBOX_CELERY_QUEUE', 'default')


def backoff_seconds(attempts: int) -> int:
    return min(BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0)), BACKOFF_MAX_SECONDS)


def enqueue(kind: str, payload: dict, *, key: str, organization_id=None,
            expires_at=None, max_attempts: int = 5):
    """
    Record an outbound side effect in the current transaction.

    ``key`` is the idempotency key: enqueueing the same key twice returns the
    existing row and sends nothing new. Returns the ``OutboxMessage``.
    """
    if kind not in HANDLERS:
        raise ValueError(f'Unknown outbox kind: {kind}')
    Outbox = _model()
    msg, created = Outbox.objects.get_or_create(
        idempotency_key=key,
        defaults={
            'kind': kind,
            'payload': json.dumps(payload),
            'organization_id': organization_id,
            'expires_at': expires_at,
            'max_attempts': max_attempts,
            'available_at': timezone.now(),
        },
    )
    if created:
        msg_id = str(msg.pk)
        transaction.on_commit(lambda: _schedule(msg_id))
    return msg


def _schedule(msg_id: str, countdown: int | None = None) -> None:
    from apps.core.tasks import dispatch_outbox_message
    try:
        dispatch_outbox_message.apply_async((msg_id,), countdown=countdown, queue=queue_name())
    except Exception:
        # Broker down: the row stays pending and relay_due picks it up.
        logger.warning('outbox.schedule_failed id=%s (relay will retry)', msg_id, exc_info=True)


def _claim(msg_id):
    """Atomically take the lease on a due message. Returns the row or None."""
    Outbox = _model()
    now = timezone.now()
    due = (
        Q(status=Outbox.Status.PENDING, available_at__lte=now)
        | Q(status=Outbox.Status.SENDING, locked_until__lt=now)
    )
    claimed = Outbox.objects.filter(due, pk=msg_id).update(
        status=Outbox.Status.SENDING,
        locked_until=now + LEASE,
        attempts=F('attempts') + 1,
    )
    return Outbox.objects.get(pk=msg_id) if claimed else None


def _finish(msg, status, *, error='', provider_ref=''):
    msg.status = status
    msg.locked_until = None
    msg.last_error = error[:2000]
    fields = ['status', 'locked_until', 'last_error']
    if provider_ref:
        msg.provider_ref = provider_ref[:200]
        fields.append('provider_ref')
    if status == msg.Status.SENT:
        msg.sent_at = timezone.now()
        fields.append('sent_at')
    if msg.kind in SENSITIVE_KINDS:
        msg.payload = '{}'
        fields.append('payload')
    msg.save(update_fields=fields)


def dispatch(msg_id) -> str:
    """
    Deliver one message. Safe to call concurrently and repeatedly: only the
    caller that wins the lease sends. Returns the resulting status (or 'skipped').
    """
    msg = _claim(msg_id)
    if msg is None:
        return 'skipped'
    Status = msg.Status

    if msg.expires_at and msg.expires_at <= timezone.now():
        _finish(msg, Status.EXPIRED, error='expired before delivery')
        logger.info('outbox.expired id=%s kind=%s', msg.pk, msg.kind)
        return Status.EXPIRED

    try:
        handler = import_string(HANDLERS[msg.kind])
        ref = handler(msg, json.loads(msg.payload or '{}'))
    except PermanentDeliveryError as exc:
        _finish(msg, Status.FAILED, error=f'permanent: {exc}')
        logger.warning('outbox.failed_permanent id=%s kind=%s: %s', msg.pk, msg.kind, exc)
        return Status.FAILED
    except Exception as exc:  # transient: retry with backoff
        err = f'{type(exc).__name__}: {exc}'
        if msg.attempts >= msg.max_attempts:
            _finish(msg, Status.FAILED, error=f'attempts exhausted: {err}')
            logger.error('outbox.dead id=%s kind=%s attempts=%s: %s',
                         msg.pk, msg.kind, msg.attempts, err)
            return Status.FAILED
        delay = backoff_seconds(msg.attempts)
        msg.status = Status.PENDING
        msg.locked_until = None
        msg.available_at = timezone.now() + timedelta(seconds=delay)
        msg.last_error = err[:2000]
        msg.save(update_fields=['status', 'locked_until', 'available_at', 'last_error'])
        logger.warning('outbox.retry id=%s kind=%s attempt=%s in=%ss: %s',
                       msg.pk, msg.kind, msg.attempts, delay, err)
        _schedule(str(msg.pk), countdown=delay)
        return Status.PENDING

    _finish(msg, Status.SENT, provider_ref=ref or '')
    logger.info('outbox.sent id=%s kind=%s', msg.pk, msg.kind)
    return Status.SENT


def due_ids(limit: int = 200) -> list[str]:
    """Ids of pending-and-due messages plus sends whose lease expired."""
    Outbox = _model()
    now = timezone.now()
    return [str(pk) for pk in Outbox.objects.filter(
        Q(status=Outbox.Status.PENDING, available_at__lte=now)
        | Q(status=Outbox.Status.SENDING, locked_until__lt=now)
    ).order_by('available_at').values_list('pk', flat=True)[:limit]]


def relay_due(limit: int = 200) -> int:
    """Queue a dispatch for every due message. Duplicate queueing is harmless (lease)."""
    ids = due_ids(limit)
    for msg_id in ids:
        _schedule(msg_id)
    if ids:
        logger.info('outbox.relay queued=%s', len(ids))
    return len(ids)


def purge(sent_days: int = 7, failed_days: int = 30) -> int:
    """Delete delivered/expired rows after ``sent_days`` and failed rows after ``failed_days``."""
    Outbox = _model()
    now = timezone.now()
    deleted, _ = Outbox.objects.filter(
        Q(status__in=[Outbox.Status.SENT, Outbox.Status.EXPIRED],
          created_at__lt=now - timedelta(days=sent_days))
        | Q(status=Outbox.Status.FAILED, created_at__lt=now - timedelta(days=failed_days))
    ).delete()
    return deleted
