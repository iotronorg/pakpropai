"""
WhatsApp message router.
Receives parsed webhook payloads, routes to PakPropAgent, sends replies.

The FSM/keyword logic is replaced entirely by the AI agent — it handles
all conversation flows, tool use, and context natively.
"""
import logging

from django.utils import timezone

from apps.users.models import User
from .client import WhatsAppClient
from .models import WhatsAppMessage, WhatsAppSession

logger = logging.getLogger(__name__)


class MessageRouter:

    @classmethod
    def route(cls, message_data: dict, phone: str):
        user, _ = User.objects.get_or_create(
            phone=f"+{phone}", defaults={'is_active': True}
        )
        session_db, _ = WhatsAppSession.objects.get_or_create(
            phone=phone, defaults={'user': user}
        )
        session_db.user = user
        session_db.message_count += 1
        session_db.last_message_at = timezone.now()
        session_db.save(update_fields=['user', 'message_count', 'last_message_at'])

        msg_type = message_data.get('type', 'text')

        # ── Resolve message text ───────────────────────────────────────────
        if msg_type == 'audio':
            text = cls._transcribe_voice(message_data, phone)
            if not text:
                cls._send_and_log(
                    phone,
                    "I received your voice message but couldn't transcribe it. "
                    "Please type your message or try again.",
                    session_db,
                )
                return
            display_body = f"[voice] {text}"

        elif msg_type == 'image':
            caption = message_data.get('image', {}).get('caption', '')
            image_bytes, mime = cls._download_media(
                message_data.get('image', {}).get('id'),
                message_data.get('image', {}).get('mime_type', 'image/jpeg'),
            )
            if image_bytes:
                reply = cls._handle_image(phone, image_bytes, mime, caption, user)
                cls._send_and_log(phone, reply, session_db)
                cls._log_inbound(message_data, session_db, caption or '[image]', msg_type)
                return
            text = caption or "I received an image but couldn't download it."
            display_body = text

        elif msg_type == 'document':
            caption = message_data.get('document', {}).get('caption', '')
            doc_bytes, mime = cls._download_media(
                message_data.get('document', {}).get('id'),
                message_data.get('document', {}).get('mime_type', 'application/pdf'),
            )
            if doc_bytes and mime.startswith('image/'):
                reply = cls._handle_image(phone, doc_bytes, mime, caption, user)
                cls._send_and_log(phone, reply, session_db)
                cls._log_inbound(message_data, session_db, caption or '[document]', msg_type)
                return
            text = caption or "I received a document."
            display_body = text

        else:
            text = message_data.get('text', {}).get('body', '').strip()
            display_body = text

        if not text:
            return

        cls._log_inbound(message_data, session_db, display_body, msg_type)

        # ── Hard reset commands (bypass agent) ────────────────────────────
        text_lower = text.lower().strip()
        if text_lower in ('reset', '/start', 'menu', 'main menu'):
            from apps.ai.agent import get_agent
            get_agent().clear_history(phone)
            cls._send_and_log(phone, cls._greeting(), session_db)
            return

        # ── Route through AI agent ─────────────────────────────────────────
        try:
            from apps.ai.agent import get_agent
            agent  = get_agent()
            reply  = agent.chat(phone, text, user)
        except Exception:
            logger.exception(f"Agent crashed for phone={phone}")
            reply = (
                "Something went wrong on my end. Please try again in a moment.\n"
                "Type *menu* to restart."
            )

        cls._send_and_log(phone, reply, session_db)

    # ─── Image handling ───────────────────────────────────────────────────────

    @classmethod
    def _handle_image(cls, phone: str, image_bytes: bytes, mime: str,
                      caption: str, user) -> str:
        try:
            from apps.ai.agent import get_agent
            return get_agent().chat_with_image(phone, image_bytes, mime, caption, user)
        except Exception as exc:
            logger.error(f"Image analysis failed: {exc}")
            return (
                "I received your image but couldn't analyze it right now.\n"
                "For property documents, please describe what you need verified."
            )

    # ─── Media download ───────────────────────────────────────────────────────

    @classmethod
    def _download_media(cls, media_id: str, mime_type: str) -> tuple:
        if not media_id:
            return None, mime_type
        try:
            data = WhatsAppClient.download_media(media_id)
            return data, mime_type
        except Exception as exc:
            logger.error(f"Media download failed id={media_id}: {exc}")
            return None, mime_type

    # ─── Voice transcription ──────────────────────────────────────────────────

    @classmethod
    def _transcribe_voice(cls, message_data: dict, phone: str) -> str:
        audio_info = message_data.get('audio', {})
        media_id   = audio_info.get('id')
        mime_type  = audio_info.get('mime_type', 'audio/ogg')
        if not media_id:
            return ''
        try:
            audio_bytes = WhatsAppClient.download_media(media_id)
            from services.ai_orchestrator import AIOrchestrator
            transcript = AIOrchestrator.transcribe_voice(audio_bytes, mime_type)
            logger.info(f"Voice transcribed phone={phone}: {transcript[:80]}")
            return transcript
        except Exception as exc:
            logger.error(f"Voice transcription failed phone={phone}: {exc}")
            return ''

    # ─── Utilities ────────────────────────────────────────────────────────────

    @staticmethod
    def _greeting() -> str:
        return (
            "Salam! Welcome to *PakProp AI* 🏠\n\n"
            "Pakistan's real estate intelligence assistant. I can help with:\n\n"
            "• 🔍 *Property search* — text or voice\n"
            "• 📋 *List your property* for sale\n"
            "• 💰 *Tax advice* — Section 7E, CGT, rental tax\n"
            "• 🏦 *Loan eligibility* — Apna Ghar & banks\n"
            "• 🛡️ *Scam/fraud check* — verify any deal\n"
            "• 📄 *Document verification* — send a photo\n\n"
            "What would you like to do? Just ask in English or Urdu."
        )

    @classmethod
    def _log_inbound(cls, message_data: dict, session_db, body: str, msg_type: str):
        try:
            WhatsAppMessage.objects.update_or_create(
                wa_message_id=message_data.get('id', ''),
                defaults={
                    'session':     session_db,
                    'direction':   'inbound',
                    'msg_type':    msg_type,
                    'body':        body[:2000],
                    'raw_payload': message_data,
                },
            )
        except Exception:
            pass

    @classmethod
    def _send_and_log(cls, phone: str, body: str, session_db):
        try:
            resp  = WhatsAppClient.send_text(phone, body)
            wa_id = resp.get('messages', [{}])[0].get('id', '')
            WhatsAppMessage.objects.create(
                session       = session_db,
                wa_message_id = wa_id or f"out-{timezone.now().timestamp()}",
                direction     = 'outbound',
                msg_type      = 'text',
                body          = body[:2000],
                raw_payload   = resp,
            )
        except Exception as exc:
            logger.error(f"Failed to send reply to {phone}: {exc}")
