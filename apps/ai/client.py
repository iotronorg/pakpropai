import hashlib
import json
import logging
import time

import google.generativeai as genai
from django.conf import settings
from django.core.cache import cache

from .models import AIInteraction

logger = logging.getLogger(__name__)

genai.configure(api_key=settings.GEMINI_API_KEY)


class GeminiClient:

    # DEFAULT_MODEL = 'gemini-1.5-flash'
    DEFAULT_MODEL = 'gemini-3-flash-preview' 
    PRO_MODEL     = 'gemini-1.5-pro'

    @classmethod
    def _cache_key(cls, model: str, prompt: str) -> str:
        h = hashlib.sha256(f"{model}::{prompt}".encode()).hexdigest()
        return f"ai:response:{h}"

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
        model = model or cls.DEFAULT_MODEL
        cache_key = cls._cache_key(model, prompt)

        if use_cache:
            cached = cache.get(cache_key)
            if cached:
                cls._log(user, interaction_type, model, 0, 0, cached_hit=True, response_ms=0,
                         input_data={'prompt_preview': prompt[:300]}, output_data={'preview': cached[:300]})
                return cached

        started = time.time()
        try:
            generation_config = {
                'max_output_tokens': max_output_tokens,
                'temperature':       temperature,
            }
            if expect_json:
                generation_config['response_mime_type'] = 'application/json'

            gen_model = genai.GenerativeModel(model_name=model, generation_config=generation_config)
            response  = gen_model.generate_content(prompt)
            text      = response.text or ''
        except Exception as exc:
            logger.error(f"Gemini call failed ({model}): {exc}")
            cls._record_failure()
            raise

        elapsed_ms = int((time.time() - started) * 1000)

        usage = getattr(response, 'usage_metadata', None)
        prompt_tokens   = getattr(usage, 'prompt_token_count', 0) if usage else 0
        response_tokens = getattr(usage, 'candidates_token_count', 0) if usage else 0

        if use_cache:
            cache.set(cache_key, text, cache_ttl)

        cls._log(user, interaction_type, model, prompt_tokens, response_tokens,
                 cached_hit=False, response_ms=elapsed_ms,
                 input_data={'prompt_preview': prompt[:300]},
                 output_data={'preview': text[:300]})
        return text

    # @classmethod
    # def generate_json(cls, prompt: str, **kwargs) -> dict:
    #     text = cls.generate(prompt, expect_json=True, **kwargs)
    #     try:
    #         return json.loads(text)
    #     except json.JSONDecodeError:
    #         logger.error(f"Gemini returned non-JSON: {text[:300]}")
    #         return {}

    @classmethod
    def generate_json(cls, prompt: str, **kwargs) -> dict:
        text = cls.generate(prompt, expect_json=True, **kwargs)
        return cls._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Extract JSON even if Gemini wraps it in prose or markdown fences."""
        import json
        import re

        if not text:
            return {}

        # Try straight parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strip ```json ... ``` fences
        fenced = re.search(r'```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                pass

        # Last resort: grab the first {...} block in the text
        brace = re.search(r'\{.*\}', text, re.DOTALL)
        if brace:
            try:
                return json.loads(brace.group(0))
            except json.JSONDecodeError:
                pass

        logger.error(f"Gemini returned non-JSON (full): {text[:500]}")
        return {}
    
    @classmethod
    def vision(cls, prompt: str, image_bytes: bytes, mime_type: str = 'image/jpeg', **kwargs) -> str:
        """Multimodal call — used for OCR."""
        model = genai.GenerativeModel(cls.PRO_MODEL)
        try:
            response = model.generate_content([prompt, {'mime_type': mime_type, 'data': image_bytes}])
            return response.text or ''
        except Exception as exc:
            logger.error(f"Gemini vision call failed: {exc}")
            raise

    @classmethod
    def transcribe_audio(cls, audio_bytes: bytes, mime_type: str = 'audio/ogg') -> str:
        """Transcribe a WhatsApp voice message using Gemini multimodal."""
        from services.prompt_library import render
        model = genai.GenerativeModel(cls.DEFAULT_MODEL)
        try:
            response = model.generate_content([
                render('voice_transcribe'),
                {'mime_type': mime_type, 'data': audio_bytes},
            ])
            text = (response.text or '').strip()
            cls._log(None, 'voice_transcribe', cls.DEFAULT_MODEL, 0, 0,
                     cached_hit=False, response_ms=0,
                     input_data={'mime_type': mime_type, 'bytes': len(audio_bytes)},
                     output_data={'transcript': text[:200]})
            return text
        except Exception as exc:
            logger.error(f"Gemini audio transcription failed: {exc}")
            raise

    # --- circuit breaker --------------------------------------------------

    FAIL_KEY      = 'ai:gemini:failures'
    FAIL_LIMIT    = 5
    FAIL_TIMEOUT  = 300

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

    # --- logging ----------------------------------------------------------

    @staticmethod
    def _log(user, interaction_type, model, prompt_tokens, response_tokens,
             cached_hit, response_ms, input_data, output_data):
        try:
            AIInteraction.objects.create(
                user=user,
                interaction_type=interaction_type,
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
                model_used=model,
                was_cached=cached_hit,
                response_ms=response_ms,
                input_data=input_data,
                output_data=output_data,
            )
        except Exception as exc:
            logger.warning(f"Failed to log AIInteraction: {exc}")