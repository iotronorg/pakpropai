import logging
from django.utils import timezone

from apps.users.models import User
from .client import WhatsAppClient
from .models import WhatsAppMessage, WhatsAppSession
from .sessions import SessionManager

logger = logging.getLogger(__name__)


# Keyword → intent mapping (Phase 6 will replace with Gemini)
KEYWORDS = {
    'property_search':  ['plot', 'house', 'flat', 'apartment', 'property'],
    'tax_query':        ['tax', '7e', 'fbr', 'filer'],
    'verify_doc':       ['verify', 'verification', 'document'],
    'check_loan':       ['loan', 'eligibility', 'mortgage'],
    'scam_check':       ['scam', 'fraud', 'verify agent'],
    'lock_deal':        ['token', 'escrow', 'lock'],
}


class MessageRouter:

    @classmethod
    def route(cls, message_data: dict, phone: str):
        # Persist the inbound message
        user, _   = User.objects.get_or_create(phone=f"+{phone}", defaults={'is_active': True})
        session_db, _ = WhatsAppSession.objects.get_or_create(
            phone=phone,
            defaults={'user': user},
        )
        session_db.user = user
        session_db.message_count += 1
        session_db.last_message_at = timezone.now()
        session_db.save()

        WhatsAppMessage.objects.update_or_create(
            wa_message_id=message_data.get('id', ''),
            defaults={
                'session':   session_db,
                'direction': 'inbound',
                'msg_type':  message_data.get('type', 'text'),
                'body':      cls._extract_body(message_data),
                'raw_payload': message_data,
            },
        )

        # Get text body
        text = cls._extract_body(message_data).lower().strip()
        intent = cls.classify_intent(text)

        # Hand off to handler
        try:
            response_text = cls._dispatch(intent, text, phone, user)
        except Exception as exc:
            logger.exception(f"Handler crash for intent={intent}")
            response_text = "Sorry, something went wrong. Please try again in a moment."

        # Send the response
        cls._send_and_log(phone, response_text, session_db)

    @staticmethod
    def _extract_body(msg: dict) -> str:
        t = msg.get('type')
        if t == 'text':     return msg.get('text', {}).get('body', '')
        if t == 'audio':    return '[audio message]'
        if t == 'image':    return msg.get('image', {}).get('caption', '[image]')
        if t == 'document': return msg.get('document', {}).get('caption', '[document]')
        return ''

    # @classmethod
    # def classify_intent(cls, text: str) -> str:
    #     text = text.lower()
    #     for intent, words in KEYWORDS.items():
    #         if any(w in text for w in words):
    #             return intent
    #     if any(g in text for g in ['hi', 'hello', 'salam', 'aoa', 'start']):
    #         return 'greeting'
    #     return 'unknown'
    
    @classmethod
    def classify_intent(cls, text: str) -> str:
        # Fast keyword path first (free, instant)
        text_lower = text.lower()
        for intent, words in KEYWORDS.items():
            if any(w in text_lower for w in words):
                return intent
        if any(g in text_lower for g in ['hi', 'hello', 'salam', 'aoa', 'start']):
            return 'greeting'

        # Fall back to Gemini for ambiguous messages
        try:
            from services.ai_orchestrator import AIOrchestrator
            result = AIOrchestrator.classify_intent(text)
            return result.get('intent', 'unknown')
        except Exception as exc:
            logger.warning(f"AI intent fallback failed: {exc}")
            return 'unknown'

    @classmethod
    def _dispatch(cls, intent: str, text: str, phone: str, user) -> str:
        if intent == 'greeting':
            return ("Salam! Welcome to PakProp AI.\n\n"
                    "I can help you with:\n"
                    "• Property search\n"
                    "• Tax (7E) calculations\n"
                    "• Document verification\n"
                    "• Loan eligibility\n"
                    "• Scam check\n\n"
                    "What would you like to do?")

        if intent == 'property_search':
            return "Tell me your city, area, and budget — e.g. 'DHA Lahore, 5 marla, 3 crore'."

        if intent == 'tax_query':
            return "Send your property's market value (PKR) and filer status to estimate 7E tax."

        if intent == 'verify_doc':
            return "Send a clear photo of the property document and I'll run a verification check."

        if intent == 'check_loan':
            return "Tell me your monthly income (PKR), loan amount needed, and tenure (years)."

        # if intent == 'scam_check':
        #     return "Send the agent's name, phone, or property registry number to check for fraud flags."
        
        if intent == 'scam_check':
            # If the message has more than just the keyword, run the check now
            words = text.lower().split()
            keywords_only = all(w in ['scam', 'fraud', 'check'] for w in words)
            if keywords_only:
                return ("Send the agent's name, phone, or registry number to scan for fraud.\n"
                        "Example: 'check agent Asif File Park Gulberg registry 4471'")
            try:
                from apps.verification.services import FraudCheckService
                r = FraudCheckService.check(text, user=user)
                risk_emoji = {'low': 'LOW', 'medium': 'MED', 'high': 'HIGH'}.get(r.get('risk'), '?')
                flags = '\n'.join([f"- {f}" for f in r.get('flags', [])]) or '- No major flags'
                steps = '\n'.join([f"{i+1}. {s}" for i, s in enumerate(r.get('verify_steps', []))])
                return (f"Risk: {risk_emoji} ({r.get('risk', 'unknown').upper()})\n\n"
                        f"Flags:\n{flags}\n\n"
                        f"Recommendation: {r.get('recommendation', '-')}\n\n"
                        f"Next steps:\n{steps}")
            except Exception as exc:
                logger.exception("scam_check failed")
            return "Sorry, our scam check is briefly down. Try again in a few minutes."

        if intent == 'lock_deal':
            return "To lock a deal: share property ID, agreed price, and seller's phone."

        return ("I didn't understand that. You can ask about:\n"
                "• Property search\n• Tax (7E)\n• Verification\n• Loan\n• Scam check\n• Token lock")

    @classmethod
    def _send_and_log(cls, phone: str, body: str, session_db):
        try:
            resp = WhatsAppClient.send_text(phone, body)
            wa_id = resp.get('messages', [{}])[0].get('id', '')
            WhatsAppMessage.objects.create(
                session       = session_db,
                wa_message_id = wa_id or f"out-{timezone.now().timestamp()}",
                direction     = 'outbound',
                msg_type      = 'text',
                body          = body,
                raw_payload   = resp,
            )
        except Exception as exc:
            logger.error(f"Failed to send WA reply to {phone}: {exc}")