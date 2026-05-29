"""
Service-specific local fallbacks for when circuit breakers are OPEN.

MetaLocalBuffer    — buffers outbound WhatsApp messages in Redis for retry-on-recovery
LLMLocalFallback   — deterministic canned replies keyed by intent
WhisperLocalFallback — static transcription placeholder
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_BUFFER_MAX = 500
_BUFFER_TTL = 86400  # 24 h

_LLM_DEFAULTS: dict[str, str] = {
    'property_search': (
        "I'm temporarily unable to search properties right now. "
        "Please try again in a moment or contact our team directly."
    ),
    'deal_lock': (
        "I'm unable to process deal requests at this time. "
        "Please contact your agent directly to proceed."
    ),
    'talk_to_agent': (
        "Connecting you to an agent — one moment please."
    ),
    'unknown': (
        "I'm having a bit of trouble right now — please try again in a moment."
    ),
}

_WHISPER_PLACEHOLDER = (
    "[VOICE MESSAGE: Transcription temporarily unavailable. "
    "A team member will listen shortly.]"
)


def _r():
    from django.core.cache import caches
    try:
        return caches['default'].client.get_client()
    except Exception:
        import redis as _rlib
        from django.conf import settings
        return _rlib.from_url(getattr(settings, 'CELERY_BROKER_URL', 'redis://localhost:6379/0'))


class MetaLocalBuffer:
    """Best-effort Redis buffer for circuit-open outbound WA messages."""

    @staticmethod
    def buffer_message(org_id: str, phone: str, text: str) -> None:
        try:
            r = _r()
            key = f'wa_buffer:{org_id}'
            entry = json.dumps({'phone': phone, 'text': text})
            pipe = r.pipeline()
            pipe.rpush(key, entry)
            pipe.ltrim(key, -_BUFFER_MAX, -1)
            pipe.expire(key, _BUFFER_TTL)
            pipe.execute()
            logger.warning('MetaLocalBuffer.buffered org=%s phone=%s', org_id, phone)
        except Exception:
            logger.warning('MetaLocalBuffer.buffer_message failed org=%s', org_id, exc_info=True)

    @staticmethod
    def flush_buffer(org_id: str) -> list[dict]:
        try:
            r = _r()
            key = f'wa_buffer:{org_id}'
            pipe = r.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            raw_list, _ = pipe.execute()
            return [json.loads(item) for item in raw_list]
        except Exception:
            logger.warning('MetaLocalBuffer.flush_buffer failed org=%s', org_id, exc_info=True)
            return []


class LLMLocalFallback:
    """Deterministic canned replies when the LLM circuit is OPEN."""

    @staticmethod
    def reply(intent: str = 'unknown') -> str:
        try:
            from apps.config.services import SystemConfigService
            configured = SystemConfigService.get(f'llm_fallback_{intent}')
            if configured:
                return configured
        except Exception:
            pass
        return _LLM_DEFAULTS.get(intent, _LLM_DEFAULTS['unknown'])


class WhisperLocalFallback:
    """Static placeholder when the STT circuit is OPEN."""

    @staticmethod
    def transcribe() -> str:
        return _WHISPER_PLACEHOLDER
