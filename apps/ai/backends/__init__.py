"""
AI providers (T-1120, ADR-028).

    gemini  → GeminiBackend            (GEMINI_API_KEY / SystemConfig gemini_api_key)
    local   → OllamaBackend            (OLLAMA_BASE_URL; no quota)
    openai  → OpenAICompatibleBackend  (AI_OPENAI_BASE_URL / _API_KEY / _MODEL)

Call sites use ``apps.ai.llm`` (prompt registry, failover, logging), not
these classes directly. ``get_backend()`` returns the primary provider.
"""
from django.conf import settings

from .base import AIBackend, Completion  # noqa: F401 — re-export for importers

PROVIDERS = ('gemini', 'local', 'openai')


def make_backend(name: str) -> AIBackend:
    if name == 'local':
        from .ollama import OllamaBackend
        return OllamaBackend()
    if name == 'openai':
        from .openai_compat import OpenAICompatibleBackend
        return OpenAICompatibleBackend()
    from .gemini import GeminiBackend
    return GeminiBackend()


def get_backend() -> AIBackend:
    """The primary provider (first of AI_PROVIDER_ORDER, i.e. AI_BACKEND by default)."""
    from apps.ai.llm import provider_order
    return make_backend(provider_order()[0])


def generate(prompt: str, max_tokens: int = 400, temperature: float = 0.3) -> str:
    """Deprecated: one-shot text through the router, without a registered prompt."""
    from apps.ai.llm import complete_text
    return complete_text(prompt, max_tokens=max_tokens, temperature=temperature).text
