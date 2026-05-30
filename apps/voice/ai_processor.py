"""
AIVoiceProcessor — accumulates μ-law audio chunks in Redis then
fires a Celery task to transcribe + generate an AI voice reply.
"""
import logging

logger = logging.getLogger(__name__)

_BUFFER_KEY  = 'voice_buf:{call_sid}'
_BUFFER_MAX  = 200   # max chunks stored (LTRIM)
_BUFFER_TTL  = 300   # seconds — expired buffers are a no-op
_FLUSH_EVERY = 50    # chunks before flushing to Celery


def _get_redis():
    try:
        from django.core.cache import caches
        return caches['default'].client.get_client()
    except Exception:
        import redis as _rlib
        from django.conf import settings
        return _rlib.from_url(getattr(settings, 'CELERY_BROKER_URL', 'redis://localhost:6379/0'))


class CallAudioBuffer:

    def __init__(self, call_sid: str):
        self.call_sid = call_sid
        self._key = _BUFFER_KEY.format(call_sid=call_sid)

    def push(self, audio_bytes: bytes) -> int:
        """Append one chunk. Returns current buffer length."""
        try:
            redis_client = _get_redis()
            pipe = redis_client.pipeline()
            pipe.rpush(self._key, audio_bytes)
            pipe.ltrim(self._key, -_BUFFER_MAX, -1)
            pipe.expire(self._key, _BUFFER_TTL)
            results = pipe.execute()
            return results[0]
        except Exception as exc:
            logger.warning("CallAudioBuffer.push failed call_sid=%s: %s", self.call_sid, exc)
            return 0

    def flush(self) -> bytes:
        """Return concatenated audio bytes and clear the buffer."""
        try:
            redis_client = _get_redis()
            pipe = redis_client.pipeline()
            pipe.lrange(self._key, 0, -1)
            pipe.delete(self._key)
            results = pipe.execute()
            chunks = results[0] or []
            return b''.join(chunks)
        except Exception as exc:
            logger.warning("CallAudioBuffer.flush failed call_sid=%s: %s", self.call_sid, exc)
            return b''

    def length(self) -> int:
        try:
            return _get_redis().llen(self._key) or 0
        except Exception:
            return 0


class AIVoiceProcessor:

    @staticmethod
    def process_audio_chunk(call_sid: str, audio_bytes: bytes) -> None:
        """
        Accumulate one audio chunk. When _FLUSH_EVERY chunks are buffered,
        kick off a Celery task to transcribe + respond.
        """
        buf = CallAudioBuffer(call_sid)
        length = buf.push(audio_bytes)
        if length >= _FLUSH_EVERY:
            from apps.voice.tasks import transcribe_and_respond
            transcribe_and_respond.delay(call_sid)

    @staticmethod
    def build_voice_context(session) -> dict:
        """
        Build AI context from the lead's WhatsApp history + CRM state + property recs.
        Fail-open — returns empty dict on any error.
        """
        try:
            ctx: dict = {}
            lead = session.lead
            if not lead:
                return ctx

            ctx['lead_status'] = lead.status
            ctx['lead_score']  = lead.score

            try:
                from apps.whatsapp.models import SessionMessage
                messages = list(
                    SessionMessage.objects
                    .filter(session__lead=lead)
                    .order_by('-created_at')
                    .values('role', 'content')[:20]
                )
                ctx['wa_history'] = messages
            except Exception:
                ctx['wa_history'] = []

            try:
                from apps.properties.recommendations import recommend_for_lead
                recs = recommend_for_lead(lead, limit=3)
                ctx['property_recs'] = [
                    {'title': p.title, 'city': p.city, 'price': p.price}
                    for p in recs
                ]
            except Exception:
                ctx['property_recs'] = []

            return ctx
        except Exception as exc:
            logger.debug("AIVoiceProcessor.build_voice_context failed: %s", exc)
            return {}
