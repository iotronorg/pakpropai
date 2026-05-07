"""
PakProp AI Agent — the core intelligence of the product.

Architecture:
- Uses google.genai (new SDK) with Gemini 2.0 Flash
- Automatic Function Calling (AFC): AI decides which tools to use
- Conversation history persisted in Redis (24h TTL, last 20 turns)
- Knowledge of Pakistani real estate law embedded in system prompt
- Graceful degradation if AI is unavailable
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

HISTORY_KEY = 'ai:conv:{phone}'
HISTORY_TTL = 86400       # 24 hours
MAX_TURNS   = 20          # keep last 20 user+model turns (40 entries)


class PakPropAgent:

    PRIMARY_MODEL  = 'gemini-2.5-flash-lite'
    FALLBACK_MODEL = 'gemini-3-flash-preview'

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return self._client

    # ─── Public interface ─────────────────────────────────────────────────────

    def chat(self, phone: str, message: str, user=None) -> str:
        """
        Process a WhatsApp text message through the AI agent.
        Returns the agent's reply as plain text (WhatsApp-formatted).
        """
        if not settings.GEMINI_API_KEY:
            return (
                "AI service is not configured yet.\n"
                "Please set GEMINI_API_KEY in your environment.\n\n"
                "Get a free key at: aistudio.google.com"
            )

        from apps.ai import tools as tool_module
        tool_module.set_context(user, phone)

        history = self._load_history(phone)
        start   = time.time()

        try:
            reply = self._call_gemini(message, history)
        except Exception as exc:
            logger.error(f"PakPropAgent primary failed phone={phone}: {exc}", exc_info=True)
            reply = self._fallback(message)

        self._save_history(phone, history, message, reply)
        self._log(user, message, reply, int((time.time() - start) * 1000))
        return reply

    def chat_with_image(self, phone: str, image_bytes: bytes, mime_type: str,
                        caption: str = '', user=None) -> str:
        """Process an image message (property document, photo) through the agent."""
        if not settings.GEMINI_API_KEY:
            return "AI service not configured. Please set GEMINI_API_KEY."

        from apps.ai import tools as tool_module
        tool_module.set_context(user, phone)

        try:
            from google import genai
            from google.genai import types
            client = self._get_client()

            context = caption or "User sent a property-related image."
            contents = [
                types.Part.from_text(
                    f"The user sent an image via WhatsApp with caption: '{context}'.\n"
                    "Analyze this image in the context of Pakistani real estate. "
                    "If it's a property document, extract key fields (owner, CNIC, property address, area, registration number). "
                    "If it's a property photo, give a brief assessment. "
                    "Flag anything suspicious or tampered."
                ),
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ]

            from apps.ai.knowledge import SYSTEM_PROMPT
            response = client.models.generate_content(
                model=self.PRIMARY_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.2,
                    max_output_tokens=800,
                ),
            )
            reply = response.text or "I couldn't analyze that image. Please try again."
        except Exception as exc:
            logger.error(f"Image analysis failed: {exc}")
            reply = "Unable to analyze the image right now. Please describe what you need help with."

        history = self._load_history(phone)
        self._save_history(phone, history, f"[image: {caption or 'no caption'}]", reply)
        return reply

    def clear_history(self, phone: str):
        cache.delete(HISTORY_KEY.format(phone=phone))

    # ─── Gemini call with AFC ─────────────────────────────────────────────────

    def _call_gemini(self, message: str, history: list) -> str:
        from google import genai
        from google.genai import types
        from apps.ai.knowledge import SYSTEM_PROMPT
        from apps.ai import tools as tool_module

        client = self._get_client()

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[
                tool_module.search_properties,
                tool_module.calculate_7e_tax,
                tool_module.check_loan_eligibility,
                tool_module.run_fraud_check,
                tool_module.list_property,
            ],
            temperature=0.3,
            max_output_tokens=1024,
        )

        chat_session = client.chats.create(
            model=self.PRIMARY_MODEL,
            config=config,
            history=self._to_genai_history(history),
        )

        response = chat_session.send_message(message)
        return (response.text or '').strip() or "I'm not sure how to help with that. Can you rephrase?"

    def _fallback(self, message: str) -> str:
        """Simple single-turn fallback without tools if chat fails."""
        try:
            from google import genai
            from google.genai import types
            from apps.ai.knowledge import SYSTEM_PROMPT
            client = self._get_client()
            response = client.models.generate_content(
                model=self.FALLBACK_MODEL,
                contents=message,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.3,
                    max_output_tokens=512,
                ),
            )
            return (response.text or '').strip() or "I'm having trouble responding right now."
        except Exception as exc:
            logger.error(f"Fallback model also failed: {exc}")
            return (
                "AI service is temporarily unavailable. Please try again in a few minutes.\n\n"
                "For urgent help: type *menu* to see what I can do."
            )

    # ─── History management ───────────────────────────────────────────────────

    def _load_history(self, phone: str) -> list:
        data = cache.get(HISTORY_KEY.format(phone=phone))
        return data if isinstance(data, list) else []

    def _save_history(self, phone: str, old_history: list, user_msg: str, model_reply: str):
        updated = old_history + [
            {'role': 'user',  'text': user_msg[:2000]},
            {'role': 'model', 'text': model_reply[:2000]},
        ]
        # Trim to last MAX_TURNS complete turns (each turn = 2 entries)
        if len(updated) > MAX_TURNS * 2:
            updated = updated[-(MAX_TURNS * 2):]
        cache.set(HISTORY_KEY.format(phone=phone), updated, HISTORY_TTL)

    def _to_genai_history(self, history: list):
        """Convert stored dicts to google.genai Content objects."""
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

    # ─── Logging ──────────────────────────────────────────────────────────────

    def _log(self, user, message: str, reply: str, response_ms: int):
        try:
            from apps.ai.models import AIInteraction
            AIInteraction.objects.create(
                user=user,
                interaction_type='intent_classify',
                model_used=self.PRIMARY_MODEL,
                response_ms=response_ms,
                input_data={'message': message[:300]},
                output_data={'reply': reply[:300]},
            )
        except Exception:
            pass


# Module-level singleton factory (one instance per thread is fine)
_agent_instance = None


def get_agent() -> PakPropAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = PakPropAgent()
    return _agent_instance
