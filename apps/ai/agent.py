"""
PakProp AI Agent — the core intelligence of the product.

Architecture:
- Backend-agnostic: uses AIBackend abstraction (Gemini or Ollama)
- Conversation history persisted in Redis (24h TTL, last 20 turns)
- Knowledge of Pakistani real estate law embedded in system prompt
- Switch backend via AI_BACKEND env var: 'gemini' | 'local'
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache

from apps.ai.backends import get_backend

logger = logging.getLogger(__name__)

HISTORY_KEY = 'ai:conv:{phone}'
HISTORY_TTL = 86400       # 24 hours
MAX_TURNS   = 20          # keep last 20 user+model turns (40 entries)


class PakPropAgent:

    def __init__(self):
        self._backend = None

    def _get_backend(self):
        if self._backend is None:
            self._backend = get_backend()
            logger.info(f"PakPropAgent using backend: {self._backend.label}")
        return self._backend

    # ─── Public interface ─────────────────────────────────────────────────────

    def chat(self, phone: str, message: str, user=None) -> str:
        """Process a WhatsApp text message. Returns the agent's reply."""
        backend = self._get_backend()

        # Gemini requires an API key; local (Ollama) does not
        if backend.label.startswith('gemini') and not settings.GEMINI_API_KEY:
            return (
                "AI service is not configured yet.\n"
                "Please set GEMINI_API_KEY in your environment.\n\n"
                "Get a free key at: aistudio.google.com"
            )

        from apps.ai import tools as tool_module
        from apps.ai.knowledge import SYSTEM_PROMPT

        tool_module.set_context(user, phone)

        history = self._load_history(phone)
        start   = time.time()

        tools = [
            tool_module.search_properties,
            tool_module.calculate_7e_tax,
            tool_module.check_loan_eligibility,
            tool_module.run_fraud_check,
            tool_module.list_property,
        ]

        try:
            reply = backend.chat(message, history, tools, SYSTEM_PROMPT)
        except Exception as exc:
            logger.error(f"Backend chat failed phone={phone}: {exc}", exc_info=True)
            reply = self._error_reply()

        self._save_history(phone, history, message, reply)
        self._log(user, message, reply, int((time.time() - start) * 1000))
        return reply

    def chat_with_image(self, phone: str, image_bytes: bytes, mime_type: str,
                        caption: str = '', user=None) -> str:
        """Process an image/document message through the agent."""
        backend = self._get_backend()

        if backend.label.startswith('gemini') and not settings.GEMINI_API_KEY:
            return "AI service not configured. Please set GEMINI_API_KEY."

        from apps.ai import tools as tool_module
        from apps.ai.knowledge import SYSTEM_PROMPT

        tool_module.set_context(user, phone)

        context = caption or "User sent a property-related image."
        prompt = (
            f"The user sent an image via WhatsApp with caption: '{context}'.\n"
            "Analyze this image in the context of Pakistani real estate. "
            "If it's a property document, extract key fields (owner, CNIC, property address, "
            "area, registration number). "
            "If it's a property photo, give a brief assessment. "
            "Flag anything suspicious or tampered."
        )

        try:
            reply = backend.analyze_image(image_bytes, mime_type, prompt)
        except Exception as exc:
            logger.error(f"Image analysis failed: {exc}")
            reply = "Unable to analyze the image right now. Please describe what you need help with."

        history = self._load_history(phone)
        self._save_history(phone, history, f"[image: {caption or 'no caption'}]", reply)
        return reply

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        """Transcribe a voice message. Returns empty string if unsupported."""
        try:
            return self._get_backend().transcribe_audio(audio_bytes, mime_type)
        except Exception as exc:
            logger.error(f"Audio transcription failed: {exc}")
            return ''

    def clear_history(self, phone: str):
        cache.delete(HISTORY_KEY.format(phone=phone))

    # ─── History management ───────────────────────────────────────────────────

    def _load_history(self, phone: str) -> list:
        data = cache.get(HISTORY_KEY.format(phone=phone))
        return data if isinstance(data, list) else []

    def _save_history(self, phone: str, old_history: list, user_msg: str, model_reply: str):
        updated = old_history + [
            {'role': 'user',  'text': user_msg[:2000]},
            {'role': 'model', 'text': model_reply[:2000]},
        ]
        if len(updated) > MAX_TURNS * 2:
            updated = updated[-(MAX_TURNS * 2):]
        cache.set(HISTORY_KEY.format(phone=phone), updated, HISTORY_TTL)

    # ─── Error handling ───────────────────────────────────────────────────────

    @staticmethod
    def _error_reply() -> str:
        return (
            "AI service is temporarily unavailable. Please try again in a few minutes.\n\n"
            "For urgent help: type *menu* to see what I can do."
        )

    # ─── Logging ──────────────────────────────────────────────────────────────

    def _log(self, user, message: str, reply: str, response_ms: int):
        try:
            from apps.ai.models import AIInteraction
            AIInteraction.objects.create(
                user=user,
                interaction_type='intent_classify',
                model_used=self._backend.label if self._backend else 'unknown',
                response_ms=response_ms,
                input_data={'message': message[:300]},
                output_data={'reply': reply[:300]},
            )
        except Exception:
            pass


# Module-level singleton
_agent_instance = None


def get_agent() -> PakPropAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = PakPropAgent()
    return _agent_instance
