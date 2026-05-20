"""
Speech-to-Text pipeline for WhatsApp voice notes.

Provider cascade (first success wins):
  1. OpenAI Whisper API  — best Urdu/Arabic accuracy; exposes detected language
  2. Gemini              — fallback when OpenAI key absent or rate-limited

WhatsApp delivers voice notes as audio/ogg (Opus codec). Both providers accept
ogg natively, so no audio conversion is required for the primary path.
"""
import io
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Map MIME types to file-extension hints required by the OpenAI files API.
_MIME_TO_EXT: dict[str, str] = {
    'audio/ogg':  '.ogg',
    'audio/opus': '.ogg',
    'audio/mpeg': '.mp3',
    'audio/mp4':  '.m4a',
    'audio/amr':  '.amr',
    'audio/wav':  '.wav',
    'audio/aac':  '.aac',
}

# Context hint improves Whisper accuracy for Urdu/Arabic mixed with English.
_WHISPER_PROMPT = (
    "Real estate conversation in Pakistan or the Middle East. "
    "May include Urdu, Arabic, or English words and property terminology."
)


@dataclass
class TranscriptResult:
    text: str
    language: str    # ISO 639-1 code detected by provider ('ur', 'en', 'ar', …)
    provider: str    # 'openai_whisper' | 'gemini' | 'none'
    duration_ms: int = 0


class STTService:
    """
    Stateless façade over multiple STT providers.
    Call STTService.transcribe() — it cascades through providers until one succeeds.
    """

    @classmethod
    def transcribe(cls, audio_bytes: bytes, mime_type: str) -> TranscriptResult:
        """
        Transcribe audio bytes with automatic language detection.

        Returns TranscriptResult with empty text when all providers fail
        (graceful degradation — router handles the fallback message).
        """
        if not audio_bytes:
            logger.warning("STTService.transcribe called with empty audio bytes.")
            return TranscriptResult(text='', language='', provider='none')

        result = cls._try_openai_whisper(audio_bytes, mime_type)
        if result:
            return result

        result = cls._try_gemini(audio_bytes, mime_type)
        if result:
            return result

        logger.error("All STT providers exhausted — returning empty transcript.")
        return TranscriptResult(text='', language='', provider='none')

    # ── Provider implementations ───────────────────────────────────────────────

    @classmethod
    def _try_openai_whisper(
        cls, audio_bytes: bytes, mime_type: str
    ) -> Optional[TranscriptResult]:
        """Attempt transcription via OpenAI Whisper API (whisper-1 model)."""
        from django.conf import settings as _s
        from apps.config.services import SystemConfigService

        api_key = (
            SystemConfigService.get('openai_api_key')
            or getattr(_s, 'OPENAI_API_KEY', '')
        )
        if not api_key:
            logger.debug("STT: OpenAI key not configured — skipping Whisper.")
            return None

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            ext      = _MIME_TO_EXT.get(mime_type, '.ogg')
            filename = f"voice{ext}"

            t0 = time.monotonic()
            response = client.audio.transcriptions.create(
                model='whisper-1',
                file=(filename, io.BytesIO(audio_bytes), mime_type),
                response_format='verbose_json',
                prompt=_WHISPER_PROMPT,
            )
            elapsed = int((time.monotonic() - t0) * 1000)

            text     = (response.text or '').strip()
            language = getattr(response, 'language', '') or ''

            if not text:
                logger.debug("STT: Whisper returned empty transcript.")
                return None

            logger.info(
                "STT: Whisper ok lang=%s len=%d took=%dms",
                language, len(text), elapsed,
            )
            return TranscriptResult(
                text=text,
                language=language,
                provider='openai_whisper',
                duration_ms=elapsed,
            )

        except openai.RateLimitError:
            logger.warning("STT: Whisper rate limit — falling back to Gemini.")
        except openai.AuthenticationError:
            logger.error("STT: OpenAI API key rejected — check openai_api_key config.")
        except openai.BadRequestError as exc:
            # Corrupted / unsupported audio format
            logger.warning("STT: Whisper rejected audio (corrupted or unsupported): %s", exc)
        except Exception as exc:
            logger.error("STT: Whisper unexpected error: %s", exc, exc_info=True)

        return None

    @classmethod
    def _try_gemini(
        cls, audio_bytes: bytes, mime_type: str
    ) -> Optional[TranscriptResult]:
        """Fallback transcription via Gemini (already configured for the AI backend)."""
        try:
            from django.conf import settings as _s
            from apps.config.services import SystemConfigService
            from apps.ai.backends.gemini import GeminiBackend

            gemini_key = (
                SystemConfigService.get('gemini_api_key')
                or getattr(_s, 'GEMINI_API_KEY', '')
            )
            if not gemini_key:
                logger.debug("STT: Gemini key not configured — skipping fallback.")
                return None

            t0      = time.monotonic()
            backend = GeminiBackend()
            text    = backend.transcribe_audio(audio_bytes, mime_type)
            elapsed = int((time.monotonic() - t0) * 1000)

            text = (text or '').strip()
            if not text:
                return None

            logger.info(
                "STT: Gemini fallback ok len=%d took=%dms", len(text), elapsed
            )
            return TranscriptResult(
                text=text,
                language='',   # Gemini does not expose the detected language code
                provider='gemini',
                duration_ms=elapsed,
            )

        except Exception as exc:
            logger.error("STT: Gemini fallback error: %s", exc, exc_info=True)

        return None
