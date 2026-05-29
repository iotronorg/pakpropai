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
from apps.core.metrics import whatsapp_messages_total

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
from .client import WhatsAppClient, get_wa_client
from .models import WhatsAppMessage, WhatsAppSession

logger = logging.getLogger(__name__)


class MessageRouter:

    @classmethod
    def _resolve_org(cls, phone_number_id: str):
        """Return the active Organization that owns this WA phone_number_id, or None."""
        if not phone_number_id:
            return None
        try:
            from apps.organizations.models import Organization
            return Organization.objects.filter(
                wa_phone_number_id=phone_number_id, is_active=True
            ).first()
        except Exception:
            return None

    @classmethod
    def route(cls, message_data: dict, phone: str, phone_number_id: str = ''):
        org  = cls._resolve_org(phone_number_id)
        user, _ = User.objects.get_or_create(
            phone=f"+{phone}", defaults={'is_active': True}
        )
        session_db, _ = WhatsAppSession.objects.get_or_create(
            phone=phone, defaults={'user': user}
        )
        session_db.user = user
        session_db.message_count += 1
        session_db.last_message_at = timezone.now()
        update_fields = ['user', 'message_count', 'last_message_at']
        if org is not None and session_db.organization_id != org.pk:
            session_db.organization = org
            update_fields.append('organization')
        session_db.save(update_fields=update_fields)

        # Deactivated clients cannot use the service.
        if not user.is_active:
            cls._send_and_log(
                phone,
                "⛔ Your account has been suspended. Please contact support for assistance.",
                session_db,
                org=org,
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
                org=org,
            )
            return

        # Every WhatsApp client interaction auto-registers the user as a lead.
        # This is a fire-and-forget upsert — never blocks message processing.
        lead = None
        try:
            from apps.leads.utils import upsert_lead
            from apps.leads.models import Lead
            upsert_lead(user, '', organization=org)
            lead = Lead.objects.filter(user=user, organization=org).order_by('-created_at').first()
        except Exception:
            pass

        # Detect Business Directory click-to-chat entry signals.
        if lead:
            try:
                from .discovery_engine import DirectoryEntryHandler
                DirectoryEntryHandler.handle(message_data, session_db, lead)
            except Exception:
                logger.warning("DirectoryEntryHandler failed for phone=%s", phone, exc_info=True)

        msg_type = message_data.get('type', 'text')
        whatsapp_messages_total.labels(message_type=msg_type, direction='inbound').inc()

        # ── BLOCKED (AML): return canned reply, no AI, no agent ──────────────
        if session_db.conversation_mode == WhatsAppSession.ConversationMode.BLOCKED:
            cls._send_and_log(
                phone,
                "Your account access has been suspended pending compliance review. "
                "Please contact support for assistance.",
                session_db,
                org=org,
            )
            return

        # ── AGENT_MANAGED: broadcast to agent room, skip LLM entirely ─────────
        if session_db.conversation_mode == WhatsAppSession.ConversationMode.AGENT_MANAGED:
            _raw_body = message_data.get('text', {}).get('body', '') or f'[{msg_type}]'
            cls._broadcast_to_agent_room(message_data, session_db, org, msg_type)
            cls._log_inbound(message_data, session_db, _raw_body, msg_type)
            if getattr(session_db, 'copilot_active', False):
                from apps.whatsapp.tasks import process_copilot_recommendations
                process_copilot_recommendations.delay(session_id=str(session_db.id))
            return

        # ── Resolve message text ───────────────────────────────────────────
        if msg_type == 'audio':
            from apps.config.services import SystemConfigService
            if not SystemConfigService.get_features().get('feature_voice_messages', True):
                cls._send_and_log(
                    phone,
                    "Voice messages are not supported at the moment. Please type your message.\n"
                    "Voice messages kay liye support abhi available nahi — please type karein.",
                    session_db,
                    org=org,
                )
                return
            text = cls._transcribe_voice(message_data, phone, org=org)
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
                org=org,
            )
            if image_bytes:
                from .sessions import SessionManager as _SM
                _sess = _SM.get(phone)
                if _sess.get('state') == 'LISTING_PHOTOS':
                    reply = cls._handle_listing_photo(phone, image_bytes, mime, user, _sess)
                else:
                    reply = cls._handle_image(phone, image_bytes, mime, caption, user, org)
                cls._send_and_log(phone, reply, session_db, org=org)
                cls._log_inbound(message_data, session_db, caption or '[image]', msg_type)
                return
            text = caption or "I received an image but couldn't download it."
            display_body = text

        elif msg_type == 'document':
            caption = message_data.get('document', {}).get('caption', '')
            doc_bytes, mime = cls._download_media(
                message_data.get('document', {}).get('id'),
                message_data.get('document', {}).get('mime_type', 'application/pdf'),
                org=org,
            )
            if doc_bytes and mime.startswith('image/'):
                reply = cls._handle_image(phone, doc_bytes, mime, caption, user, org)
                cls._send_and_log(phone, reply, session_db, org=org)
                cls._log_inbound(message_data, session_db, caption or '[document]', msg_type)
                return
            text = caption or "I received a document."
            display_body = text

        elif msg_type == 'location':
            loc = message_data.get('location', {})
            lat = loc.get('latitude')
            lon = loc.get('longitude')
            if lat is not None and lon is not None and org is not None and lead is not None:
                try:
                    from .discovery_engine import GeoContextResolver
                    nearby = GeoContextResolver.resolve(float(lat), float(lon), org, lead, session_db)
                    count  = nearby.count()
                    city   = session_db.context.get('geo', {}).get('city') or 'your area'
                    text   = (
                        f"[System: User dropped a location pin at lat={lat}, lon={lon}. "
                        f"Resolved city: {city}. {count} active properties match nearby. "
                        f"Property IDs: {list(nearby.values_list('id', flat=True)[:5])}. "
                        f"Present relevant nearby listings naturally.]"
                    )
                except Exception:
                    logger.warning("GeoContextResolver failed for phone=%s", phone, exc_info=True)
                    text = "[User dropped a location pin. Ask what area they are interested in.]"
            else:
                text = "[User dropped a location pin. Ask what area they are interested in.]"
            display_body = '[location pin]'

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
                    org=org,
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
            get_agent().clear_history(phone, org=org)
            cls._send_and_log(phone, cls._greeting(), session_db, org=org)
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
                cls._send_and_log(phone, _msg, session_db, org=org)
                return
            else:
                _remaining = cls._MAX_WA_PHOTOS - _sess.get('context', {}).get('photo_count', 0)
                cls._send_and_log(
                    phone,
                    f"Send a photo to add it to your listing ({_remaining} slot{'s' if _remaining > 1 else ''} left), "
                    "or type *done* to finish.",
                    session_db,
                    org=org,
                )
                return

        # ── Route through AI Service Manager ──────────────────────────────────
        # Note: gateway-level rate limiting (15/min) is handled in WhatsAppWebhookView
        # before messages are queued. No second rate-limit layer needed here.
        # Adds guardrails, intent pre-classification, dynamic context injection,
        # and direct tool routing for deterministic intents (scam check, tax calc).
        try:
            from apps.ai.service import get_service_manager
            svc   = get_service_manager()
            logger.debug("[MSG] phone=%s msg=%r", phone, text[:60])
            reply = svc.process(phone, text, user, organization=org)
        except Exception:
            logger.exception(f"ServiceManager crashed for phone={phone}")
            reply = (
                "Something went wrong on my end. Please try again in a moment.\n"
                "Type *menu* to restart."
            )

        cls._send_and_log(phone, reply, session_db, org=org)

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
                      caption: str, user, org=None) -> str:
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
                return agent.verify_document_image(phone, image_bytes, mime, caption, user, organization=org)
            return agent.chat_with_image(phone, image_bytes, mime, caption, user, organization=org)
        except Exception as exc:
            logger.error(f"Image analysis failed: {exc}")
            return (
                "I received your image but couldn't analyze it right now.\n"
                "For property documents, send the photo with a caption like:\n"
                "*'verify fard'*, *'check allotment letter'*, or *'scan NOC'*"
            )

    # ─── Media download ───────────────────────────────────────────────────────

    @classmethod
    def _download_media(cls, media_id: str, mime_type: str, org=None) -> tuple:
        if not media_id:
            return None, mime_type
        try:
            data = get_wa_client(org).download_media(media_id)
            return data, mime_type
        except Exception as exc:
            logger.error(f"Media download failed id={media_id}: {exc}")
            return None, mime_type

    # ─── Voice transcription ──────────────────────────────────────────────────

    @classmethod
    def _transcribe_voice(cls, message_data: dict, phone: str, org=None) -> str:
        audio_info = message_data.get('audio', {})

        # Fast-path: dedicated transcribe_audio_task already ran STT — skip re-download.
        if (pre := audio_info.get('_transcript')) is not None:
            logger.debug("[AUDIO] phone=%s using pre-fetched transcript len=%d", phone, len(pre))
            return pre

        media_id  = audio_info.get('id')
        mime_type = audio_info.get('mime_type', 'audio/ogg')
        if not media_id:
            logger.warning(f"Audio message from {phone} has no media_id — skipping download")
            return ''
        try:
            logger.debug(f"[AUDIO] phone={phone} downloading media_id={media_id}")
            audio_bytes = get_wa_client(org).download_media(media_id)
            logger.debug(f"[AUDIO] downloaded {len(audio_bytes)} bytes, mime={mime_type}")
            from apps.ai.agent import get_agent
            from apps.resilience.resilience_engine import whisper_stt_circuit
            from apps.resilience.fallbacks import WhisperLocalFallback
            transcript = whisper_stt_circuit.call(
                get_agent().transcribe_audio,
                audio_bytes, mime_type,
                fallback_fn=WhisperLocalFallback.transcribe,
            )
            if transcript:
                logger.debug(f"[AUDIO] transcribed: {transcript[:80]!r}")
            else:
                logger.debug("[AUDIO] transcription returned empty (no Gemini key in local mode?)")
            logger.info(f"Voice transcribed phone={phone}: {transcript[:80] if transcript else '<empty>'}")
            return transcript
        except Exception as exc:
            logger.error(f"Voice transcription failed phone={phone}: {exc}", exc_info=True)
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
    def _broadcast_to_agent_room(cls, message_data: dict, session_db, org, msg_type: str):
        """
        Broadcast a raw WhatsApp payload to the org's agent room group.
        Fire-and-forget — no agent connected means the client gets no reply (correct).
        """
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        from django.utils import timezone as _tz

        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning("No channel layer configured — cannot broadcast to agent room.")
            return

        group_name = f"org_{org.id}_agent_room"
        lead_id = cls._resolve_lead_id(session_db, org)

        payload = {
            "type":       "agent_message",
            "event":      "inbound_message",
            "session_id": str(session_db.id),
            "phone":      session_db.phone,
            "message":    message_data,
            "msg_type":   msg_type,
            "timestamp":  _tz.now().isoformat(),
            "lead_id":    str(lead_id) if lead_id else None,
        }
        try:
            async_to_sync(channel_layer.group_send)(group_name, payload)
        except Exception:
            logger.warning(f"group_send to {group_name} failed", exc_info=True)

    @classmethod
    def _resolve_lead_id(cls, session_db, org):
        try:
            from apps.leads.models import Lead
            return (
                Lead.objects.filter(user=session_db.user, organization=org)
                .values_list('id', flat=True)
                .first()
            )
        except Exception:
            return None

    @classmethod
    def _log_inbound(cls, message_data: dict, session_db, body: str, msg_type: str):
        wa_message_id = message_data.get('id', '')

        # Extract the WhatsApp media object ID for non-text messages so it can be
        # used later for deferred/lazy download without re-parsing raw_payload.
        _media_map = {'audio': 'audio', 'image': 'image', 'document': 'document'}
        media_id  = ''
        media_url = ''
        if msg_type in _media_map:
            _media_obj = message_data.get(_media_map[msg_type], {})
            media_id   = _media_obj.get('id', '')
            media_url  = _media_obj.get('_cdn_url', '')  # populated by dedicated media workers

        try:
            WhatsAppMessage.objects.update_or_create(
                wa_message_id=wa_message_id,
                defaults={
                    'session':     session_db,
                    'direction':   'inbound',
                    'msg_type':    msg_type,
                    'body':        body[:2000],
                    'media_id':    media_id,
                    'media_url':   media_url,
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
    def _send_and_log(cls, phone: str, body: str, session_db, org=None):
        try:
            client = get_wa_client(org)
            resp   = client.send_text(phone, body, skip_window_check=True)
            wa_id  = resp.get('messages', [{}])[0].get('id', '') or f"out-{timezone.now().timestamp()}"
            WhatsAppMessage.objects.create(
                session       = session_db,
                wa_message_id = wa_id,
                direction     = 'outbound',
                msg_type      = 'text',
                body          = body[:2000],
                raw_payload   = resp,
            )
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
