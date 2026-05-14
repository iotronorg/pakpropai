"""
Gemini backend — uses google.genai with Automatic Function Calling (AFC).
The SDK handles the tool-call loop internally.
"""
import logging

from django.conf import settings

from .base import AIBackend

logger = logging.getLogger(__name__)


class GeminiBackend(AIBackend):

    def __init__(self):
        self._client = None
        self._api_key = None

    def _client_instance(self):
        from apps.config.services import SystemConfigService
        key = SystemConfigService.get('gemini_api_key') or settings.GEMINI_API_KEY
        if self._client is None or key != self._api_key:
            from google import genai
            self._client = genai.Client(api_key=key)
            self._api_key = key
        return self._client

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
