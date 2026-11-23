"""
Gemini backend — uses google.genai with Automatic Function Calling (AFC).
The SDK handles the tool-call loop internally.
"""
import logging

from django.conf import settings

from apps.ai.usage import record_gemini_response

from .base import AIBackend, Completion

logger = logging.getLogger(__name__)


def _api_key() -> str:
    from apps.config.services import SystemConfigService
    return SystemConfigService.get('gemini_api_key') or settings.GEMINI_API_KEY


class GeminiBackend(AIBackend):
    name = 'gemini'

    def __init__(self):
        self._client = None
        self._api_key = None

    def is_configured(self) -> bool:
        return bool(_api_key())

    def _client_instance(self):
        key = _api_key()
        if self._client is None or key != self._api_key:
            from google import genai
            from google.genai import types
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=int(settings.AI_TIMEOUT_S * 1000)))
            self._api_key = key
        return self._client

    def complete(self, prompt, *, system='', max_tokens=512, temperature=0.2,
                 json_mode=False, timeout=30.0) -> Completion:
        from google.genai import types
        cfg = types.GenerateContentConfig(max_output_tokens=max_tokens, temperature=temperature,
                                          system_instruction=system or None,
                                          http_options=types.HttpOptions(timeout=int(timeout * 1000)))
        if json_mode:
            cfg.response_mime_type = 'application/json'
        model = settings.GEMINI_MODEL
        response = self._client_instance().models.generate_content(model=model, contents=prompt, config=cfg)
        record_gemini_response(model, response)
        meta = getattr(response, 'usage_metadata', None)
        return Completion(text=(response.text or '').strip(), model=model,
                          prompt_tokens=getattr(meta, 'prompt_token_count', 0) or 0,
                          completion_tokens=getattr(meta, 'candidates_token_count', 0) or 0)

    # ── AIBackend interface ───────────────────────────────────────────────────

    def chat(self, message: str, history: list,
             tools: list, system_prompt: str) -> str:
        from google.genai import types
        client = self._client_instance()

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=tools,           # Python callables → AFC handles the loop
            temperature=0.3,
            max_output_tokens=1024,
        )
        chat_session = client.chats.create(
            model=settings.GEMINI_MODEL,
            config=config,
            history=self._to_contents(history),
        )
        response = chat_session.send_message(message)
        # With Automatic Function Calling, usage_metadata covers the final model
        # call only (intermediate tool rounds are not reported) — KNOWN_ISSUES KI-006.
        record_gemini_response(settings.GEMINI_MODEL, response)
        return (response.text or '').strip()

    def analyze_image(self, image_bytes: bytes, mime_type: str,
                      prompt: str) -> str:
        from google import genai
        from google.genai import types
        client = self._client_instance()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[
                types.Part.from_text(prompt),
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=800, temperature=0.1,
            ),
        )
        record_gemini_response(settings.GEMINI_MODEL, response)
        return (response.text or '').strip()

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        from google import genai
        from google.genai import types
        client = self._client_instance()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[
                types.Part.from_text(
                    "Transcribe this voice message exactly as spoken. "
                    "Output ONLY the transcription."
                ),
                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=512, temperature=0.1,
            ),
        )
        record_gemini_response(settings.GEMINI_MODEL, response)
        return (response.text or '').strip()

    @property
    def label(self) -> str:
        return f"gemini:{settings.GEMINI_MODEL}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_contents(history: list):
        from google.genai import types
        result = []
        for item in history:
            text = item.get('text', '').strip()
            role = item.get('role', 'user')
            if text and role in ('user', 'model'):
                result.append(
                    types.Content(role=role, parts=[types.Part(text=text)])
                )
        return result
