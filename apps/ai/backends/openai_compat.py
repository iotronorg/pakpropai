"""
OpenAI-compatible provider (T-1120): OpenAI, Azure OpenAI, Groq, Together, vLLM,
LM Studio … any server speaking /v1/chat/completions. Reuses the Ollama tool loop.

    AI_OPENAI_BASE_URL=https://api.openai.com/v1
    AI_OPENAI_API_KEY=sk-...           (secret)
    AI_OPENAI_MODEL=<model id>

Unconfigured (no key or no model) → skipped by the router. Paid per token.
"""
from django.conf import settings

from .ollama import OllamaBackend


class OpenAICompatibleBackend(OllamaBackend):
    name = 'openai'

    def _base_url(self) -> str:
        return settings.AI_OPENAI_BASE_URL.rstrip('/')

    def _api_key(self) -> str:
        return settings.AI_OPENAI_API_KEY

    @property
    def _model(self) -> str:
        return settings.AI_OPENAI_MODEL

    def is_configured(self) -> bool:
        return bool(settings.AI_OPENAI_BASE_URL and settings.AI_OPENAI_API_KEY and settings.AI_OPENAI_MODEL)

    def analyze_image(self, image_bytes, mime_type, prompt):
        # Vision needs a vision-capable model; use the configured one.
        from apps.ai.usage import record_openai_response
        import base64
        b64 = base64.b64encode(image_bytes).decode()
        response = self._get_client().chat.completions.create(
            model=self._model, max_tokens=800,
            messages=[{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': f"data:{mime_type};base64,{b64}"}}]}])
        record_openai_response(self._model, response)
        return (response.choices[0].message.content or '').strip()
