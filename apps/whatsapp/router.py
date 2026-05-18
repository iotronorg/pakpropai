"""
WhatsApp message router.
Receives parsed webhook payloads, routes to RealTronAgent, sends replies.

The FSM/keyword logic is replaced entirely by the AI agent — it handles
all conversation flows, tool use, and context natively.
"""
import logging
import re
import unicodedata

from django.utils import timezone

# ── Prompt injection sanitization ─────────────────────────────────────────────

_INJECTION_PATTERNS = re.compile(
    r'(ignore\s+(all\s+)?(previous|prior|above)\s+instructions?'
    r'|you\s+are\s+now\s+'
    r'|new\s+instructions?:'
    r'|system\s*:'
    r'|assistant\s*:'
    r'|<\s*/?(?:system|instructions?|prompt)\s*>'
    r'|act\s+as\s+(if\s+you\s+are|a\s+)'
    r'|pretend\s+(you\s+are|to\s+be)'
    r'|disregard\s+(all|your)\s+'
    r'|your\s+(true|real|actual)\s+(purpose|role|instructions?)'
    r'|forget\s+(all\s+)?previous\s+'
    r'|do\s+anything\s+now'
    r'|dan\s+mode)',
    re.IGNORECASE,
)

_MAX_TEXT_LEN = 2000


def _sanitize(text: str) -> str:
    """
    Strip control chars, null bytes, zero-width unicode, and obvious injection phrases.
    Returns clean text or raises ValueError if the message is a clear injection attempt.
    """
    # Remove null bytes and control characters (keep newlines and tabs)
    text = ''.join(
        ch for ch in text
        if ch in ('\n', '\t') or (not unicodedata.category(ch).startswith('C'))
    )
    # Collapse excessive whitespace runs
    text = re.sub(r'[ \t]{4,}', '   ', text)
    # Truncate
    text = text[:_MAX_TEXT_LEN]
    # Detect injection attempts
    if _INJECTION_PATTERNS.search(text):
        raise ValueError("Prompt injection attempt detected.")
    return text.strip()

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

        # Deactivated clients cannot use the service.
        if not user.is_active:
            cls._send_and_log(
                phone,
                "⛔ Your account has been suspended. Please contact support for assistance.",
                session_db,
            )
            return

        # Non-client roles (agent, developer, admin) use the web dashboard.
        # If they message the bot, redirect them and stop processing.
        if user.role != 'client':
            cls._send_and_log(
                phone,
                (
                    "👋 Hi! Your account has dashboard access.\n\n"
                    "Please use the *RealTron AI dashboard* to manage your "
                    "listings, leads, and settings.\n\n"
                    "This WhatsApp number is for property buyers and clients only."
                ),
                session_db,
            )
            return

        # Every WhatsApp client interaction auto-registers the user as a lead.
        # This is a fire-and-forget upsert — never blocks message processing.
        try:
            from apps.whatsapp.handlers import _upsert_lead
            _upsert_lead(user)
        except Exception:
            pass

        msg_type = message_data.get('type', 'text')

        # ── Resolve message text ───────────────────────────────────────────
        if msg_type == 'audio':
            from apps.config.services import SystemConfigService
            if not SystemConfigService.get_features().get('feature_voice_messages', True):
                cls._send_and_log(
                    phone,
                    "Voice messages are not supported at the moment. Please type your message.\n"
                    "Voice messages kay liye support abhi available nahi — please type karein.",
                    session_db,
                )
                return
            text = cls._transcribe_voice(message_data, phone)
            if not text:
                # Transcription failed (unsupported backend or download error).
                # Route a generic prompt to the AI so the user still gets a useful reply
                # rather than a dead-end error message.
                text = (
                    "The user sent a voice message but it could not be transcribed. "
                    "Apologize briefly, explain that voice messages need a Gemini API key to work "
                    "when using the local AI backend, and ask them to type their question instead. "
                    "Keep it short and bilingual (English + Urdu)."
                )
            display_body = f"[voice] {text}"

        elif msg_type == 'image':
            caption = message_data.get('image', {}).get('caption', '')
            image_bytes, mime = cls._download_media(
                message_data.get('image', {}).get('id'),
                message_data.get('image', {}).get('mime_type', 'image/jpeg'),
            )
            if image_bytes:
                from .sessions import SessionManager as _SM
                _sess = _SM.get(phone)
                if _sess.get('state') == 'LISTING_PHOTOS':
                    reply = cls._handle_listing_photo(phone, image_bytes, mime, user, _sess)
                else:
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
            raw_text = message_data.get('text', {}).get('body', '').strip()
            try:
                text = _sanitize(raw_text)
            except ValueError:
                logger.warning(f"Injection attempt from {phone}: {raw_text[:100]!r}")
                cls._send_and_log(
                    phone,
                    "I can't process that message. Please ask about properties, verification, or tax advice.",
                    session_db,
                )
                return
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

        # ── LISTING_PHOTOS: intercept text (done/skip) ────────────────────
        from .sessions import SessionManager as _SM
        _sess = _SM.get(phone)
        if _sess.get('state') == 'LISTING_PHOTOS':
            _done_words = {'done', 'skip', 'finish', 'khatam', 'bas', 'ok',
                           'okay', 'no', 'cancel', 'quit', 'end', 'stop'}
            if text_lower in _done_words or text_lower.startswith('done'):
                _photo_count = _sess.get('context', {}).get('photo_count', 0)
                _SM.update(phone, state='IDLE', context={})
                if _photo_count:
                    _msg = (
                        f"Done! {_photo_count} photo{'s' if _photo_count > 1 else ''} added "
                        f"to your listing.\n\nYour property is live and visible to buyers. "
                        "Type *menu* for more options."
                    )
                else:
                    _msg = (
                        "Listing complete with no photos.\n\n"
                        "You can always add photos later from the web portal. "
                        "Type *menu* for more options."
                    )
                cls._send_and_log(phone, _msg, session_db)
                return
            else:
                _remaining = cls._MAX_WA_PHOTOS - _sess.get('context', {}).get('photo_count', 0)
                cls._send_and_log(
                    phone,
                    f"Send a photo to add it to your listing ({_remaining} slot{'s' if _remaining > 1 else ''} left), "
                    "or type *done* to finish.",
                    session_db,
                )
                return

        # ── Per-user AI rate limit (10 requests/min/phone) ────────────────
        if not cls._check_rate_limit(phone):
            cls._send_and_log(
                phone,
                "You're sending messages too fast. Please wait a moment and try again.",
                session_db,
            )
            return

        # ── Route through AI agent ─────────────────────────────────────────
        try:
            from apps.ai.agent import get_agent
            agent   = get_agent()
            backend = agent._get_backend().label
            print(f"\033[93m[MSG] phone={phone} backend={backend} msg={text[:60]!r}\033[0m")
            reply   = agent.chat(phone, text, user)
        except Exception:
            logger.exception(f"Agent crashed for phone={phone}")
            reply = (
                "Something went wrong on my end. Please try again in a moment.\n"
                "Type *menu* to restart."
            )

        cls._send_and_log(phone, reply, session_db)

    # ─── Listing photo upload ────────────────────────────────────────────────

    _MAX_WA_PHOTOS = 5

    @classmethod
    def _handle_listing_photo(cls, phone: str, image_bytes: bytes, mime: str, user, session: dict) -> str:
        from .sessions import SessionManager
        ctx         = session.get('context', {})
        prop_id     = ctx.get('property_id')
        photo_count = ctx.get('photo_count', 0)

        if not prop_id:
            SessionManager.update(phone, state='IDLE', context={})
            return (
                "Something went wrong finding your listing. "
                "Your property is saved — add photos any time from the web portal."
            )

        try:
            import uuid
            from django.core.files.base import ContentFile
            from apps.properties.models import Property, PropertyImage

            prop = Property.objects.get(id=prop_id, owner=user)
            ext  = 'jpg' if 'jpeg' in mime else mime.split('/')[-1]

            pi = PropertyImage(property=prop, uploaded_by=user, order=photo_count)
            pi.image.save(f'{uuid.uuid4().hex}.{ext}', ContentFile(image_bytes), save=True)

            photo_count += 1
            remaining = cls._MAX_WA_PHOTOS - photo_count

            if photo_count >= cls._MAX_WA_PHOTOS:
                SessionManager.update(phone, state='IDLE', context={})
                return (
                    f"Photo {photo_count}/{cls._MAX_WA_PHOTOS} added. "
                    f"Maximum reached — your listing is complete with {photo_count} photos. "
                    "Buyers can view them online.\n\nType *menu* for more options."
                )

            SessionManager.update(phone, state='LISTING_PHOTOS', context={
                'property_id': prop_id,
                'photo_count': photo_count,
            })
            return (
                f"Photo {photo_count}/{cls._MAX_WA_PHOTOS} added.\n\n"
                f"Send another photo or type *done* to finish. "
                f"({remaining} slot{'s' if remaining > 1 else ''} remaining)"
            )

        except Property.DoesNotExist:
            SessionManager.update(phone, state='IDLE', context={})
            return "Couldn't find your listing. Type *menu* to start over."
        except Exception as exc:
            logger.error(f"WA photo upload failed phone={phone}: {exc}")
            return "Couldn't save that photo. Please try again or type *done* to finish."

    # ─── Image handling ───────────────────────────────────────────────────────

    _DOC_CAPTION_KEYWORDS = {
        'verify', 'check', 'fard', 'allotment', 'deed', 'registry',
        'noc', 'document', 'doc', 'certificate', 'cnic', 'poa',
        'scan', 'ocr', 'read', 'tassdeq', 'tasdeeq',
    }

    @classmethod
    def _is_document_request(cls, caption: str) -> bool:
        cap = caption.lower()
        return any(kw in cap for kw in cls._DOC_CAPTION_KEYWORDS)

    @classmethod
    def _handle_image(cls, phone: str, image_bytes: bytes, mime: str,
                      caption: str, user) -> str:
        try:
            from apps.ai.agent import get_agent
            from apps.config.services import SystemConfigService
            agent = get_agent()
            if cls._is_document_request(caption):
                if not SystemConfigService.get_features().get('feature_document_verification', True):
                    return (
                        "Document verification is not currently available.\n"
                        "Please contact support for assistance."
                    )
                return agent.verify_document_image(phone, image_bytes, mime, caption, user)
            return agent.chat_with_image(phone, image_bytes, mime, caption, user)
        except Exception as exc:
            logger.error(f"Image analysis failed: {exc}")
            return (
                "I received your image but couldn't analyze it right now.\n"
                "For property documents, send the photo with a caption like:\n"
                "*'verify fard'*, *'check allotment letter'*, or *'scan NOC'*"
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
            logger.warning(f"Audio message from {phone} has no media_id — skipping download")
            return ''
        try:
            print(f"\033[94m[AUDIO] phone={phone} downloading media_id={media_id}\033[0m")
            audio_bytes = WhatsAppClient.download_media(media_id)
            print(f"\033[94m[AUDIO] downloaded {len(audio_bytes)} bytes, mime={mime_type}\033[0m")
            from apps.ai.agent import get_agent
            transcript = get_agent().transcribe_audio(audio_bytes, mime_type)
            if transcript:
                print(f"\033[94m[AUDIO] transcribed: {transcript[:80]!r}\033[0m")
            else:
                print(f"\033[94m[AUDIO] transcription returned empty (no Gemini key in local mode?)\033[0m")
            logger.info(f"Voice transcribed phone={phone}: {transcript[:80] if transcript else '<empty>'}")
            return transcript
        except Exception as exc:
            logger.error(f"Voice transcription failed phone={phone}: {exc}", exc_info=True)
            print(f"\033[91m[AUDIO ERROR] phone={phone}: {exc}\033[0m")
            return ''

    # ─── Utilities ────────────────────────────────────────────────────────────

    @classmethod
    def _greeting(cls) -> str:
        from apps.config.services import SystemConfigService
        features = SystemConfigService.get_features()
        LINES = {
            'feature_property_search':       "• 🔍 *Property search* — text or voice",
            'feature_property_listing':      "• 📋 *List your property* for sale",
            'feature_tax_advice':            "• 💰 *Tax advice* — Section 7E, CGT, rental tax",
            'feature_loan_eligibility':      "• 🏦 *Loan eligibility* — Apna Ghar & banks",
            'feature_scam_check':            "• 🛡️ *Scam/fraud check* — verify any deal",
            'feature_document_verification': "• 📄 *Document verification* — send a photo",
            'feature_talk_to_agent':         "• 🤝 *Talk to an agent* — connect with a verified agent",
            'feature_deal_lock':             "• 🔒 *Deal Lock* — reserve a property (token payment)",
            'feature_property_audit':        "• 📊 *Property Audit* — detailed risk & investment report",
        }
        lines = '\n'.join(v for k, v in LINES.items() if features.get(k, True))
        return (
            "Salam! Welcome to *RealTron AI* 🏠\n\n"
            "Your AI-powered real estate assistant. I can help with:\n\n"
            f"{lines}\n\n"
            "What would you like to do? Just ask in English or Urdu."
        )

    @classmethod
    def _log_inbound(cls, message_data: dict, session_db, body: str, msg_type: str):
        wa_message_id = message_data.get('id', '')
        try:
            WhatsAppMessage.objects.update_or_create(
                wa_message_id=wa_message_id,
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
        # Mirror inbound messages to CRM conversation log (enables dashboard chat view)
        cls._persist_crm_inbound(session_db.user, body, wa_message_id)

    @classmethod
    def _persist_crm_inbound(cls, user, body: str, wa_message_id: str):
        try:
            from apps.leads.models import Lead, ConversationMessage
            lead = Lead.objects.filter(user=user).order_by('-created_at').first()
            if lead and body:
                ConversationMessage.objects.get_or_create(
                    wa_message_id=wa_message_id,
                    defaults={
                        'lead':      lead,
                        'direction': ConversationMessage.Direction.INBOUND,
                        'channel':   ConversationMessage.Channel.WHATSAPP,
                        'body':      body[:2000],
                    },
                )
        except Exception:
            pass

    @classmethod
    def _check_rate_limit(cls, phone: str, limit: int = 10, window: int = 60) -> bool:
        """Fixed-window rate limit: returns False if phone exceeds `limit` msgs in `window` seconds."""
        from django.core.cache import cache
        key = f"wa:rate:{phone}"
        # cache.add sets key=0 with TTL only if absent — preserves existing TTL on subsequent calls
        cache.add(key, 0, timeout=window)
        try:
            count = cache.incr(key)
        except ValueError:
            # Race: key expired between add and incr
            cache.set(key, 1, timeout=window)
            return True
        return count <= limit

    @classmethod
    def _send_and_log(cls, phone: str, body: str, session_db):
        try:
            resp  = WhatsAppClient.send_text(phone, body, skip_window_check=True)
            wa_id = resp.get('messages', [{}])[0].get('id', '') or f"out-{timezone.now().timestamp()}"
            WhatsAppMessage.objects.create(
                session       = session_db,
                wa_message_id = wa_id,
                direction     = 'outbound',
                msg_type      = 'text',
                body          = body[:2000],
                raw_payload   = resp,
            )
            # Mirror outbound replies to CRM so agents see full two-way conversations
            cls._persist_crm_outbound(session_db.user, body, wa_id)
        except Exception as exc:
            logger.error(f"Failed to send reply to {phone}: {exc}")

    @classmethod
    def _persist_crm_outbound(cls, user, body: str, wa_message_id: str):
        try:
            from apps.leads.models import Lead, ConversationMessage
            lead = Lead.objects.filter(user=user).order_by('-created_at').first()
            if lead and body:
                ConversationMessage.objects.get_or_create(
                    wa_message_id=wa_message_id,
                    defaults={
                        'lead':      lead,
                        'direction': ConversationMessage.Direction.OUTBOUND,
                        'channel':   ConversationMessage.Channel.WHATSAPP,
                        'body':      body[:2000],
                    },
                )
        except Exception:
            pass
