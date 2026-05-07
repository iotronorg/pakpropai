"""
Low-level Gemini client using the new google.genai SDK.
Used by AIOrchestrator for non-conversational tasks (OCR, property scoring, voice transcription).
Conversational chat goes through apps.ai.agent.PakPropAgent instead.
"""
import hashlib
import json
import logging
import re
import time

from django.conf import settings
from django.core.cache import cache

from .models import AIInteraction

logger = logging.getLogger(__name__)


def _get_client():
    from google import genai
    return genai.Client(api_key=settings.GEMINI_API_KEY)


class GeminiClient:

    DEFAULT_MODEL = 'gemini-2.5-flash-lite'
    PRO_MODEL     = 'gemini-2.5-flash-lite'

    FAIL_KEY     = 'ai:gemini:failures'
    FAIL_LIMIT   = 5
    FAIL_TIMEOUT = 300

    # ─── Text generation ──────────────────────────────────────────────────────

    @classmethod
    def generate(cls, prompt: str, *,
                 model: str = None,
                 user=None,
                 interaction_type: str = 'intent_classify',
                 max_output_tokens: int = 512,
                 temperature: float = 0.2,
                 cache_ttl: int = 3600,
                 use_cache: bool = True,
                 expect_json: bool = False) -> str:

        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not configured")

        model = model or cls.DEFAULT_MODEL
        cache_key = f"ai:resp:{hashlib.sha256(f'{model}::{prompt}'.encode()).hexdigest()}"

        if use_cache:
            cached = cache.get(cache_key)
            if cached:
                cls._log(user, interaction_type, model, 0, 0, True, 0,
                         {'prompt_preview': prompt[:200]}, {'preview': cached[:200]})
                return cached

        started = time.time()
        try:
            from google.genai import types
            client = _get_client()
            cfg = types.GenerateContentConfig(
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
            if expect_json:
                cfg.response_mime_type = 'application/json'

            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=cfg,
            )
            text = (response.text or '').strip()
        except Exception as exc:
            logger.error(f"GeminiClient.generate failed ({model}): {exc}")
            cls._record_failure()
            raise

        elapsed_ms = int((time.time() - started) * 1000)

        usage = getattr(response, 'usage_metadata', None)
        ptok = getattr(usage, 'prompt_token_count', 0) if usage else 0
        rtok = getattr(usage, 'candidates_token_count', 0) if usage else 0

        if use_cache and text:
            cache.set(cache_key, text, cache_ttl)

        cls._log(user, interaction_type, model, ptok, rtok, False, elapsed_ms,
                 {'prompt_preview': prompt[:200]}, {'preview': text[:200]})
        return text

    @classmethod
    def generate_json(cls, prompt: str, **kwargs) -> dict:
        text = cls.generate(prompt, expect_json=True, **kwargs)
        return cls._parse_json(text)

    # ─── Vision / OCR ─────────────────────────────────────────────────────────

    @classmethod
    def vision(cls, prompt: str, image_bytes: bytes,
               mime_type: str = 'image/jpeg', **kwargs) -> str:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not configured")
        try:
            from google import genai
            from google.genai import types
            client = _get_client()
            response = client.models.generate_content(
                model=cls.PRO_MODEL,
                contents=[
                    types.Part.from_text(prompt),
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(
                    max_output_tokens=1024,
                    temperature=0.1,
                ),
            )
            return (response.text or '').strip()
        except Exception as exc:
            logger.error(f"GeminiClient.vision failed: {exc}")
            cls._record_failure()
            raise

    # ─── Audio transcription ──────────────────────────────────────────────────

    @classmethod
    def transcribe_audio(cls, audio_bytes: bytes,
                         mime_type: str = 'audio/ogg') -> str:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not configured")
        try:
            from google import genai
            from google.genai import types
            client = _get_client()
            response = client.models.generate_content(
                model=cls.DEFAULT_MODEL,
                contents=[
                    types.Part.from_text(
                        "Transcribe this voice message exactly as spoken. "
                        "Output ONLY the transcription, no labels or explanation."
                    ),
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(
                    max_output_tokens=512,
                    temperature=0.1,
                ),
            )
            text = (response.text or '').strip()
            cls._log(None, 'voice_transcribe', cls.DEFAULT_MODEL, 0, 0, False, 0,
                     {'mime_type': mime_type, 'bytes': len(audio_bytes)},
                     {'transcript': text[:200]})
            return text
        except Exception as exc:
            logger.error(f"GeminiClient.transcribe_audio failed: {exc}")
            cls._record_failure()
            raise

    # ─── Circuit breaker ──────────────────────────────────────────────────────

    @classmethod
    def _record_failure(cls):
        try:
            count = cache.get(cls.FAIL_KEY, 0)
            cache.set(cls.FAIL_KEY, count + 1, cls.FAIL_TIMEOUT)
        except Exception:
            pass

    @classmethod
    def is_healthy(cls) -> bool:
        return cache.get(cls.FAIL_KEY, 0) < cls.FAIL_LIMIT

    # ─── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(text: str) -> dict:
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        fenced = re.search(r'```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                pass
        brace = re.search(r'\{.*\}', text, re.DOTALL)
        if brace:
            try:
                return json.loads(brace.group(0))
            except json.JSONDecodeError:
                pass
        logger.error(f"Could not parse JSON from Gemini response: {text[:300]}")
        return {}

    @staticmethod
    def _log(user, interaction_type, model, ptok, rtok, cached, ms, inp, out):
        try:
            AIInteraction.objects.create(
                user=user,
                interaction_type=interaction_type,
                prompt_tokens=ptok,
                response_tokens=rtok,
                model_used=model,
                was_cached=cached,
                response_ms=ms,
                input_data=inp,
                output_data=out,
            )
        except Exception as exc:
            logger.warning(f"AIInteraction log failed: {exc}")
