"""
Backend factory — returns the active AIBackend based on settings.AI_BACKEND.

    AI_BACKEND=gemini   → GeminiBackend  (default; uses GEMINI_API_KEY)
    AI_BACKEND=local    → OllamaBackend  (local Ollama server; no quota limits)
"""
from django.conf import settings

from .base import AIBackend  # noqa: F401 — re-export for importers


def get_backend() -> AIBackend:
    backend = getattr(settings, 'AI_BACKEND', 'gemini').lower()
    if backend == 'local':
        from .ollama import OllamaBackend
        return OllamaBackend()
    from .gemini import GeminiBackend
    return GeminiBackend()
