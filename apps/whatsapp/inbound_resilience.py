"""
Inbound WhatsApp resilience (T-1020 / KI-019).

Under DB connection starvation, inbound messages used to be dropped: the
worker logged the exception and returned. This module gives the worker:

* ``TRANSIENT_DB_ERRORS`` — errors worth retrying (connection refused, pool
  exhausted, server closed the connection).
* ``track_side_effects()`` — counts user-visible side effects (outbound sends,
  agent-room broadcasts) during one processing attempt. A retry is only safe
  when nothing has been sent yet; otherwise the user would get a duplicate reply.
* A Redis dead-letter list for messages whose retries are exhausted, plus
  ``replay_dead_letters()`` (management command ``replay_whatsapp_dead_letters``).

Redis is used (not the DB) because the DB is exactly what is failing.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import InterfaceError, OperationalError
from django.utils import timezone

logger = logging.getLogger(__name__)

TRANSIENT_DB_ERRORS = (OperationalError, InterfaceError)

DEAD_LETTER_KEY = 'wa:inbound:deadletter'
DEAD_LETTER_MAX = 10_000


@dataclass
class SideEffects:
    count: int = 0


_current: contextvars.ContextVar[SideEffects | None] = contextvars.ContextVar(
    'wa_inbound_side_effects', default=None,
)


@contextlib.contextmanager
def track_side_effects():
    state = SideEffects()
    token = _current.set(state)
    try:
        yield state
    finally:
        _current.reset(token)


def mark_side_effect() -> None:
    """Record that a user-visible side effect was attempted (no-op outside tracking)."""
    state = _current.get()
    if state is not None:
        state.count += 1


def retry_countdown(retries: int) -> int:
    """Exponential backoff: 5, 10, 20, 40, 80, 160 s (capped at 300 s)."""
    return min(300, 5 * (2 ** retries))


def _redis():
    import redis
    return redis.Redis.from_url(settings.REDIS_URL)


def dead_letter(message: dict, phone_number_id: str, otel_context: str, error: Exception) -> bool:
    """Park a message that could not be processed. Returns False if Redis is also down."""
    record = {
        'message': message,
        'phone_number_id': phone_number_id,
        'otel_context': otel_context or '',
        'error': f'{type(error).__name__}: {error}'[:500],
        'failed_at': timezone.now().isoformat(),
    }
    try:
        client = _redis()
        pipe = client.pipeline()
        pipe.lpush(DEAD_LETTER_KEY, json.dumps(record))
        pipe.ltrim(DEAD_LETTER_KEY, 0, DEAD_LETTER_MAX - 1)
        pipe.execute()
    except Exception:
        logger.critical(
            'WA inbound dead-letter write failed — message LOST msg_id=%s phone_number_id=%s',
            message.get('id'), phone_number_id, exc_info=True,
        )
        return False
    logger.error(
        'WA inbound message dead-lettered after retries msg_id=%s error=%s',
        message.get('id'), record['error'],
    )
    return True


def dead_letter_count() -> int:
    return int(_redis().llen(DEAD_LETTER_KEY))


def replay_dead_letters(limit: int | None = None) -> int:
    """Re-enqueue parked messages oldest-first. Returns the number re-enqueued."""
    from .tasks import process_incoming_whatsapp_task

    client = _redis()
    replayed = 0
    while limit is None or replayed < limit:
        raw = client.rpop(DEAD_LETTER_KEY)
        if raw is None:
            break
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            logger.error('WA dead-letter: unparseable record dropped: %r', raw[:200])
            continue
        try:
            process_incoming_whatsapp_task.delay(
                record['message'], record.get('phone_number_id', ''),
                otel_context=record.get('otel_context', ''),
            )
        except Exception:
            client.rpush(DEAD_LETTER_KEY, raw)  # put it back at the oldest end
            raise
        replayed += 1
    return replayed
