import logging
from django.utils import timezone

from apps.users.models import User
from .client import WhatsAppClient
from .models import WhatsAppMessage, WhatsAppSession
from .sessions import SessionManager

logger = logging.getLogger(__name__)

KEYWORDS = {
    'property_search': ['plot', 'house', 'flat', 'apartment', 'property', 'marla', 'kanal', 'find me'],
    'list_property':   ['list property', 'add listing', 'list my', 'sell my', 'add property', 'post listing'],
    'tax_query':       ['tax', '7e', 'fbr', 'filer', 'section 7'],
    'verify_doc':      ['verify', 'verification', 'document', 'registry'],
    'check_loan':      ['loan', 'eligibility', 'mortgage', 'apna ghar'],
    'scam_check':      ['scam', 'fraud', 'check agent'],
    'lock_deal':       ['token', 'escrow', 'lock deal'],
}

_GREETINGS = ('hi', 'hello', 'salam', 'aoa', 'start', 'assalam', '/start')

# States where the user is in the middle of a multi-turn flow
_FLOW_STATES = {
    'AWAITING_TAX_INPUT',
    'AWAITING_LOAN_INPUT',
    'AWAITING_PROPERTY_DETAILS',
    'SEARCH_PAGINATING',
    'LISTING_CITY',
    'LISTING_LOCATION',
    'LISTING_DETAILS',
    'LISTING_PRICE',
    'LISTING_CONFIRM',
}


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
        session_db.save()

        msg_type = message_data.get('type', 'text')
        raw_body = cls._extract_body(message_data)

        # Transcribe voice before routing
        if msg_type == 'audio':
            text = cls._transcribe_voice(message_data, phone)
            if not text:
                cls._send_and_log(
                    phone,
                    "I received your voice message but couldn't transcribe it. "
                    "Please try again or type your message.",
                    session_db,
                )
                return
        else:
            text = raw_body

        WhatsAppMessage.objects.update_or_create(
            wa_message_id=message_data.get('id', ''),
            defaults={
                'session':     session_db,
                'direction':   'inbound',
                'msg_type':    msg_type,
                'body':        text or raw_body,
                'raw_payload': message_data,
            },
        )

        session = SessionManager.get(phone)
        try:
            response_text = cls._handle(text.strip(), phone, user, session)
        except Exception:
            logger.exception(f"Handler crash phone={phone}")
            response_text = "Sorry, something went wrong. Please try again in a moment."

        cls._send_and_log(phone, response_text, session_db)

    # ── FSM core ──────────────────────────────────────────────────────────────

    @classmethod
    def _handle(cls, text: str, phone: str, user, session: dict) -> str:
        state = session.get('state', 'IDLE')
        ctx   = session.get('context', {})

        # Global reset
        if text.lower().strip() in ('cancel', 'menu', 'main menu', 'back', 'reset', '/start'):
            SessionManager.update(phone, state='IDLE', context={})
            return cls._greeting()

        # "more" — paginate previous search
        if text.lower().strip() in ('more', 'next', 'show more', 'aur') and state in ('SEARCH_PAGINATING', 'IDLE'):
            from .handlers import handle_more_results
            reply, new_state, ctx_patch = handle_more_results(phone, user, ctx)
            ctx.update(ctx_patch)
            SessionManager.update(phone, state=new_state, context=ctx)
            return reply

        # Route by active state
        if state == 'AWAITING_TAX_INPUT':
            from .handlers import handle_tax_input
            reply, new_state, ctx_patch = handle_tax_input(text, phone, user, ctx)

        elif state == 'AWAITING_LOAN_INPUT':
            from .handlers import handle_loan_input
            reply, new_state, ctx_patch = handle_loan_input(text, phone, user, ctx)

        elif state in ('AWAITING_PROPERTY_DETAILS', 'SEARCH_PAGINATING'):
            from .handlers import handle_property_details
            reply, new_state, ctx_patch = handle_property_details(text, phone, user, ctx)

        elif state == 'LISTING_CITY':
            from .handlers import handle_listing_city
            reply, new_state, ctx_patch = handle_listing_city(text, phone, user, ctx)

        elif state == 'LISTING_LOCATION':
            from .handlers import handle_listing_location
            reply, new_state, ctx_patch = handle_listing_location(text, phone, user, ctx)

        elif state == 'LISTING_DETAILS':
            from .handlers import handle_listing_details
            reply, new_state, ctx_patch = handle_listing_details(text, phone, user, ctx)

        elif state == 'LISTING_PRICE':
            from .handlers import handle_listing_price
            reply, new_state, ctx_patch = handle_listing_price(text, phone, user, ctx)

        elif state == 'LISTING_CONFIRM':
            from .handlers import handle_listing_confirm
            reply, new_state, ctx_patch = handle_listing_confirm(text, phone, user, ctx)

        else:
            intent = cls.classify_intent(text)
            reply, new_state, ctx_patch = cls._start_flow(intent, text, phone, user, ctx)

        ctx.update(ctx_patch)
        SessionManager.update(phone, state=new_state, context=ctx)
        return reply

    @classmethod
    def _start_flow(cls, intent: str, text: str, phone: str, user, ctx: dict) -> tuple:
        from .handlers import (
            start_tax_flow, start_loan_flow,
            start_property_search, start_listing_flow,
        )
        from apps.verification.services import FraudCheckService

        if intent == 'greeting':
            return cls._greeting(), 'IDLE', {}

        if intent == 'tax_query':
            return start_tax_flow(text, phone, user, ctx)

        if intent == 'check_loan':
            return start_loan_flow(text, phone, user, ctx)

        if intent == 'property_search':
            return start_property_search(text, phone, user, ctx)

        if intent == 'list_property':
            return start_listing_flow(text, phone, user, ctx)

        if intent == 'verify_doc':
            return (
                "Send a clear photo of the property document and I'll run a verification check.",
                'IDLE', {}
            )

        if intent == 'scam_check':
            words        = text.lower().split()
            keyword_only = all(w in {'scam', 'fraud', 'check', 'verify', 'agent'} for w in words)
            if keyword_only:
                return (
                    "Send the agent's name, phone, or registry number to scan for fraud.\n"
                    "Example: *check agent Asif File Park Gulberg registry 4471*",
                    'IDLE', {}
                )
            try:
                r          = FraudCheckService.check(text, user=user)
                risk_label = {'low': 'LOW', 'medium': 'MED', 'high': 'HIGH'}.get(r.get('risk'), '?')
                flags      = '\n'.join(f"- {f}" for f in r.get('flags', [])) or '- No major flags'
                steps      = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(r.get('verify_steps', [])))
                reply = (f"Risk: {risk_label} ({r.get('risk', 'unknown').upper()})\n\n"
                         f"Flags:\n{flags}\n\n"
                         f"Recommendation: {r.get('recommendation', '-')}\n\n"
                         f"Next steps:\n{steps}")
            except Exception:
                logger.exception("scam_check failed")
                reply = "Scam check is briefly down. Try again in a few minutes."
            return reply, 'IDLE', {}

        if intent == 'lock_deal':
            return "To lock a deal: share property ID, agreed price, and seller's phone.", 'IDLE', {}

        return (
            "I didn't understand that. You can ask about:\n"
            "• Property search (or send a voice note)\n"
            "• List a property\n"
            "• Tax (7E)\n"
            "• Loan eligibility\n"
            "• Scam check\n"
            "• Token lock",
            'IDLE', {}
        )

    # ── Intent classification ─────────────────────────────────────────────────

    @classmethod
    def classify_intent(cls, text: str) -> str:
        text_lower = text.lower()

        # Multi-word keywords must be checked before single-word ones
        for intent in ('list_property', 'scam_check', 'lock_deal'):
            if any(w in text_lower for w in KEYWORDS[intent]):
                return intent

        for intent, words in KEYWORDS.items():
            if any(w in text_lower for w in words):
                return intent

        if any(g in text_lower for g in _GREETINGS):
            return 'greeting'

        try:
            from services.ai_orchestrator import AIOrchestrator
            result = AIOrchestrator.classify_intent(text)
            return result.get('intent', 'unknown')
        except Exception as exc:
            logger.warning(f"AI intent fallback failed: {exc}")
            return 'unknown'

    # ── Voice transcription ───────────────────────────────────────────────────

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
            logger.info(f"Voice transcribed for {phone}: {transcript[:80]}")
            return transcript
        except Exception as exc:
            logger.error(f"Voice transcription failed for {phone}: {exc}")
            return ''

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_body(msg: dict) -> str:
        t = msg.get('type')
        if t == 'text':     return msg.get('text', {}).get('body', '')
        if t == 'audio':    return '[voice message]'
        if t == 'image':    return msg.get('image', {}).get('caption', '[image]')
        if t == 'document': return msg.get('document', {}).get('caption', '[document]')
        return ''

    @staticmethod
    def _greeting() -> str:
        return (
            "Salam! Welcome to PakProp AI.\n\n"
            "I can help you with:\n"
            "• Property search (text or voice)\n"
            "• List a property\n"
            "• Tax (7E) calculations\n"
            "• Loan eligibility\n"
            "• Document verification\n"
            "• Scam check\n\n"
            "What would you like to do?"
        )

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
                body          = body,
                raw_payload   = resp,
            )
        except Exception:
            logger.error(f"Failed to send WA reply to {phone}")
