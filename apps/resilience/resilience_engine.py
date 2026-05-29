"""
SLA Resilience Engine — rolling error-rate circuit breakers + outage flags.

Three named circuit breakers:
  meta_cloud_api_circuit  — Meta WhatsApp Cloud API
  whisper_stt_circuit     — Whisper/STT transcription
  llm_provider_circuit    — LLM provider (Gemini/OpenAI/Ollama)

All Redis operations are fail-open: exceptions log WARNING and return safe defaults.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 180   # 3-minute rolling window
_OPEN_TTL       = 60    # OPEN state expires after 60s → HALF_OPEN candidate
_THRESHOLD      = 0.15  # 15% error rate trips the breaker

_REGISTRY: dict[str, 'AdvancedCircuitBreaker'] = {}


# ── Redis helper (same pattern as token_governor) ─────────────────────────────

def _r():
    from django.core.cache import caches
    try:
        return caches['default'].client.get_client()
    except Exception:
        import redis as _rlib
        from django.conf import settings
        return _rlib.from_url(getattr(settings, 'CELERY_BROKER_URL', 'redis://localhost:6379/0'))


def _cache():
    from django.core.cache import cache
    return cache


# ── ErrorRateTracker ──────────────────────────────────────────────────────────

class ErrorRateTracker:
    """
    Redis sorted-set rolling-window error rate tracker.
    Keys: cb_errors:{service}, cb_successes:{service}
    Score = Unix timestamp; member = nanosecond timestamp for uniqueness.
    """

    @staticmethod
    def record(service: str, outcome: Literal['success', 'error']) -> None:
        try:
            r = _r()
            now = time.time()
            cutoff = now - _WINDOW_SECONDS
            key = f'cb_{"errors" if outcome == "error" else "successes"}:{service}'
            member = str(time.time_ns())
            pipe = r.pipeline()
            pipe.zadd(key, {member: now})
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.expire(key, _WINDOW_SECONDS * 3)
            pipe.execute()
        except Exception:
            logger.warning('ErrorRateTracker.record failed service=%s', service, exc_info=True)

    @staticmethod
    def get_error_rate(service: str) -> float:
        try:
            r = _r()
            now = time.time()
            cutoff = now - _WINDOW_SECONDS
            errors_key = f'cb_errors:{service}'
            successes_key = f'cb_successes:{service}'
            pipe = r.pipeline()
            pipe.zremrangebyscore(errors_key, 0, cutoff)
            pipe.zremrangebyscore(successes_key, 0, cutoff)
            pipe.zcard(errors_key)
            pipe.zcard(successes_key)
            results = pipe.execute()
            errors, successes = results[2], results[3]
            total = errors + successes
            if total == 0:
                return 0.0
            return min(1.0, max(0.0, errors / total))
        except Exception:
            logger.warning('ErrorRateTracker.get_error_rate failed service=%s', service, exc_info=True)
            return 0.0

    @staticmethod
    def should_trip(service: str, threshold: float = _THRESHOLD) -> bool:
        try:
            return ErrorRateTracker.get_error_rate(service) >= threshold
        except Exception:
            logger.warning('ErrorRateTracker.should_trip failed service=%s', service, exc_info=True)
            return False

    @staticmethod
    def clear(service: str) -> None:
        try:
            r = _r()
            r.delete(f'cb_errors:{service}', f'cb_successes:{service}')
        except Exception:
            logger.warning('ErrorRateTracker.clear failed service=%s', service, exc_info=True)


# ── OutageFlagService ─────────────────────────────────────────────────────────

class OutageFlagService:
    """Writes/reads outage flags used by the SLA dashboard."""

    _TTL = 120

    @staticmethod
    def set_outage(service: str, is_open: bool, error_rate: float) -> None:
        try:
            data = json.dumps({
                'is_open': is_open,
                'error_rate': round(error_rate, 4),
                'updated_at': str(time.time()),
            })
            _cache().set(f'outage_state:{service}', data, OutageFlagService._TTL)
        except Exception:
            logger.warning('OutageFlagService.set_outage failed service=%s', service, exc_info=True)

    @staticmethod
    def get_all_outages() -> dict[str, dict]:
        result = {}
        for service in ('meta_cloud_api', 'whisper_stt', 'llm_provider'):
            try:
                raw = _cache().get(f'outage_state:{service}')
                result[service] = json.loads(raw) if raw else {
                    'is_open': False, 'error_rate': 0.0, 'updated_at': '',
                }
            except Exception:
                result[service] = {'is_open': False, 'error_rate': 0.0, 'updated_at': ''}
        return result


# ── AdvancedCircuitBreaker ────────────────────────────────────────────────────

class AdvancedCircuitBreaker:
    """
    Rolling error-rate circuit breaker.

    Trip threshold: 15% error rate in 3-min rolling window (ErrorRateTracker).
    OPEN TTL: 60s — after which next call() attempts HALF_OPEN probe.
    State stored in Django cache under cb_state:{service}.

    All instances auto-register in _REGISTRY for get_all_states().
    """

    def __init__(self, service: str, threshold: float = _THRESHOLD):
        self.service   = service
        self.threshold = threshold
        _REGISTRY[service] = self

    # ── Public API ────────────────────────────────────────────────────────────

    def call(self, fn: Callable, *args, fallback_fn: Callable, **kwargs) -> Any:
        state = self.get_state()

        if state == 'OPEN':
            if self._should_recover():
                self._set_state('HALF_OPEN')
                state = 'HALF_OPEN'
            else:
                logger.warning('adv_circuit.open service=%s — using fallback', self.service)
                return fallback_fn()

        try:
            result = fn(*args, **kwargs)
            ErrorRateTracker.record(self.service, 'success')
            if state == 'HALF_OPEN':
                self._close()
            return result
        except Exception as exc:
            ErrorRateTracker.record(self.service, 'error')
            if state == 'HALF_OPEN' or ErrorRateTracker.should_trip(self.service, self.threshold):
                self._open()
            logger.warning('adv_circuit.fallback service=%s exc=%s', self.service, exc)
            return fallback_fn()

    def get_state(self) -> str:
        return _cache().get(f'cb_state:{self.service}', 'CLOSED')

    def is_open(self) -> bool:
        return self.get_state() == 'OPEN'

    def reset(self) -> None:
        """Force-reset to CLOSED and clear error-rate + outage flag."""
        _cache().delete(f'cb_state:{self.service}')
        _cache().delete(f'cb_open_at:{self.service}')
        ErrorRateTracker.clear(self.service)
        OutageFlagService.set_outage(self.service, False, 0.0)
        logger.info('adv_circuit.reset service=%s', self.service)

    @staticmethod
    def get_all_states() -> dict[str, str]:
        return {name: cb.get_state() for name, cb in _REGISTRY.items()}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _set_state(self, state: str) -> None:
        _cache().set(f'cb_state:{self.service}', state, timeout=_OPEN_TTL * 4)

    def _open(self) -> None:
        _cache().set(f'cb_state:{self.service}', 'OPEN', timeout=_OPEN_TTL * 4)
        _cache().set(f'cb_open_at:{self.service}', time.time(), timeout=_OPEN_TTL * 4)
        error_rate = ErrorRateTracker.get_error_rate(self.service)
        OutageFlagService.set_outage(self.service, True, error_rate)
        logger.error('adv_circuit.opened service=%s error_rate=%.2f', self.service, error_rate)

    def _close(self) -> None:
        _cache().delete(f'cb_state:{self.service}')
        _cache().delete(f'cb_open_at:{self.service}')
        ErrorRateTracker.clear(self.service)
        OutageFlagService.set_outage(self.service, False, 0.0)
        logger.info('adv_circuit.closed service=%s', self.service)

    def _should_recover(self) -> bool:
        opened_at = _cache().get(f'cb_open_at:{self.service}') or 0
        return (time.time() - float(opened_at)) >= _OPEN_TTL


# ── Named singletons ──────────────────────────────────────────────────────────

meta_cloud_api_circuit = AdvancedCircuitBreaker('meta_cloud_api')
whisper_stt_circuit    = AdvancedCircuitBreaker('whisper_stt')
llm_provider_circuit   = AdvancedCircuitBreaker('llm_provider')
