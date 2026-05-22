import time
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CLOSED    = 'closed'
_OPEN      = 'open'
_HALF_OPEN = 'half_open'


def _cache():
    from django.core.cache import cache
    return cache


class CircuitBreaker:
    """
    Redis-backed circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED.

    Uses Django's default cache backend (Redis in production, LocMemCache in tests).
    Keys are namespaced by `name` so multiple breakers coexist safely.
    """

    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.name               = name
        self.failure_threshold  = failure_threshold
        self.recovery_timeout   = recovery_timeout
        self._state_key   = f'circuit:{name}:state'
        self._count_key   = f'circuit:{name}:failures'
        self._open_at_key = f'circuit:{name}:opened_at'

    def call(self, fn: Callable, *args, fallback: Any = None, **kwargs) -> Any:
        state = self.get_state()

        if state == _OPEN:
            if self._should_attempt_recovery():
                self._set_state(_HALF_OPEN)
            else:
                logger.warning('circuit_breaker.open name=%s — returning fallback', self.name)
                return fallback

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            return fallback

    def get_state(self) -> str:
        return _cache().get(self._state_key, _CLOSED)

    def reset(self):
        c = _cache()
        c.delete(self._state_key)
        c.delete(self._count_key)
        c.delete(self._open_at_key)

    def _set_state(self, state: str):
        _cache().set(self._state_key, state, timeout=self.recovery_timeout * 4)

    def _on_success(self):
        if self.get_state() == _HALF_OPEN:
            logger.info('circuit_breaker.recovered name=%s', self.name)
        self.reset()

    def _on_failure(self, exc: Exception):
        c = _cache()
        failures = (c.get(self._count_key) or 0) + 1
        c.set(self._count_key, failures, timeout=self.recovery_timeout * 2)
        logger.warning('circuit_breaker.failure name=%s count=%d exc=%s', self.name, failures, exc)
        if failures >= self.failure_threshold:
            c.set(self._state_key, _OPEN, timeout=self.recovery_timeout * 4)
            c.set(self._open_at_key, time.time(), timeout=self.recovery_timeout * 4)
            logger.error('circuit_breaker.opened name=%s', self.name)

    def _should_attempt_recovery(self) -> bool:
        opened_at = _cache().get(self._open_at_key) or 0
        return (time.time() - float(opened_at)) >= self.recovery_timeout


# ── Named singletons (import these in service modules) ────────────────────────

ai_circuit       = CircuitBreaker('ai',       failure_threshold=5, recovery_timeout=60)
stripe_circuit   = CircuitBreaker('stripe',   failure_threshold=3, recovery_timeout=120)
safepay_circuit  = CircuitBreaker('safepay',  failure_threshold=3, recovery_timeout=60)
bsecure_circuit  = CircuitBreaker('bsecure',  failure_threshold=3, recovery_timeout=60)
whatsapp_circuit = CircuitBreaker('whatsapp', failure_threshold=5, recovery_timeout=30)
