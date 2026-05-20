import logging
import requests

from celery import shared_task

from apps.whatsapp.client import WA_API_URL

logger = logging.getLogger(__name__)


# ── Primary webhook dispatcher ─────────────────────────────────────────────────

@shared_task(ignore_result=True)
def process_incoming_whatsapp_task(message: dict, phone_number_id: str = ''):
    """
    Async Celery worker for a single inbound WhatsApp message.

    The webhook view returns 200 OK immediately after dispatching this task.
    Media messages (audio / image / document) are handed off to dedicated
    workers with separate resource profiles; text goes straight to the router.

    Retries are intentionally disabled: the idempotency key is set in the view
    before dispatch, so a retry would silently drop the message.
    """
    msg_id   = message.get('id', '')
    phone    = message.get('from', '')
    msg_type = message.get('type', 'text')

    if not msg_id or not phone:
        logger.warning(
            "process_incoming_whatsapp_task: missing id or from — %r", message
        )
        return

    # Show blue ticks immediately (3-15 s before AI reply).
    try:
        from apps.whatsapp.client import WhatsAppClient
        WhatsAppClient.mark_read(msg_id)
    except Exception:
        pass  # Non-critical — never block message processing

    # ── Dispatch to dedicated media workers ───────────────────────────────────
    if msg_type == 'audio':
        audio   = message.get('audio', {})
        media_id = audio.get('id', '')
        mime     = audio.get('mime_type', 'audio/ogg')
        if media_id:
            logger.info(
                "WA inbound audio phone=%s msg_id=%s media_id=%s — dispatching STT worker",
                phone, msg_id, media_id,
            )
            transcribe_audio_task.delay(media_id, mime, phone, phone_number_id, message)
            return
        # No media_id — fall through to router (edge case: forward/status message)

    elif msg_type == 'image':
        image    = message.get('image', {})
        media_id = image.get('id', '')
        mime     = image.get('mime_type', 'image/jpeg')
        caption  = image.get('caption', '')
        if media_id:
            logger.info(
                "WA inbound image phone=%s msg_id=%s media_id=%s — dispatching Vision worker",
                phone, msg_id, media_id,
            )
            process_image_task.delay(
                media_id, mime, phone, phone_number_id, caption, message
            )
            return

    elif msg_type == 'document':
        doc      = message.get('document', {})
        media_id = doc.get('id', '')
        mime     = doc.get('mime_type', 'application/pdf')
        filename = doc.get('filename', '')
        caption  = doc.get('caption', '')
        if media_id:
            logger.info(
                "WA inbound document phone=%s msg_id=%s media_id=%s filename=%s — dispatching OCR worker",
                phone, msg_id, media_id, filename,
            )
            process_document_task.delay(
                media_id, mime, phone, phone_number_id, filename, caption, message
            )
            return

    # ── WhatsApp AI token guard ────────────────────────────────────────────────
    try:
        from apps.organizations.models import Organization
        from apps.billing.ledger import UsageLedger

        org = Organization.objects.filter(wa_phone_number_id=phone_number_id).first()
        if org is not None:
            plan = getattr(org, 'plan', 'trial')
            if not UsageLedger.within_limit(str(org.id), plan, 'wa_tokens'):
                logger.warning(
                    'WA token limit exhausted org=%s plan=%s count=%d — sending canned reply',
                    org.id, plan, UsageLedger.get_wa_token_count(str(org.id)),
                )
                from apps.whatsapp.client import WhatsAppClient
                WhatsAppClient.send_text(
                    phone,
                    'Our AI assistant is at capacity. One of our agents will follow up with you shortly.',
                )
                return
    except Exception:
        logger.exception('WA token guard failed phone=%s — proceeding', phone)

    # ── Text (and media fallbacks with no media_id) → direct routing ─────────
    try:
        from apps.whatsapp.router import MessageRouter
        MessageRouter.route(message, phone, phone_number_id)
    except Exception:
        logger.exception(
            "MessageRouter crashed for phone=%s msg_id=%s type=%s",
            phone, msg_id, msg_type,
        )


# ── Dedicated media workers ────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=30, ignore_result=True)
def transcribe_audio_task(
    self,
    media_id: str,
    mime_type: str,
    phone: str,
    phone_number_id: str,
    message_data: dict,
):
    """
    STT pipeline for inbound voice notes.

    1. Download binary via WhatsAppMediaDownloader (with S3 archival)
    2. Transcribe via STTService (OpenAI Whisper → Gemini fallback)
    3. Inject transcript into message_data so MessageRouter skips re-download
    4. Route through MessageRouter → AI → reply
    """
    from apps.whatsapp.media_services import (
        WhatsAppMediaDownloader,
        MediaRateLimitError,
        MediaError,
    )
    from apps.whatsapp.stt_services import STTService

    # ── 1. Download ───────────────────────────────────────────────────────────
    try:
        result = WhatsAppMediaDownloader.download(media_id, mime_type)
    except MediaRateLimitError as exc:
        logger.warning(
            "Audio download rate-limited media_id=%s — scheduling retry %d/%d",
            media_id, self.request.retries + 1, self.max_retries,
        )
        raise self.retry(exc=exc)
    except MediaError as exc:
        logger.error(
            "Audio download failed media_id=%s phone=%s: %s — routing with empty transcript",
            media_id, phone, exc,
        )
        # Route anyway so the user gets a graceful error reply.
        message_data.setdefault('audio', {})['_transcript'] = ''
        _safe_route(message_data, phone, phone_number_id)
        return

    # ── 2. Transcribe ─────────────────────────────────────────────────────────
    transcript = STTService.transcribe(result.data, result.mime_type)
    logger.info(
        "Audio transcribed phone=%s provider=%s lang=%s len=%d",
        phone, transcript.provider, transcript.language, len(transcript.text),
    )

    # ── 3. Inject pre-fetched data into message_data ──────────────────────────
    # MessageRouter._transcribe_voice checks for '_transcript' first, skipping re-download.
    audio_meta = message_data.setdefault('audio', {})
    audio_meta['_transcript'] = transcript.text
    if result.cdn_url:
        audio_meta['_cdn_url'] = result.cdn_url

    # ── 4. Route ──────────────────────────────────────────────────────────────
    _safe_route(message_data, phone, phone_number_id)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, ignore_result=True)
def process_image_task(
    self,
    media_id: str,
    mime_type: str,
    phone: str,
    phone_number_id: str,
    caption: str,
    message_data: dict,
):
    """
    Vision/OCR pipeline for inbound images.

    Downloads the image (archiving to S3 when configured), then routes through
    MessageRouter which handles vision analysis via the existing agent.
    """
    from apps.whatsapp.media_services import (
        WhatsAppMediaDownloader,
        MediaRateLimitError,
        MediaError,
    )

    try:
        result = WhatsAppMediaDownloader.download(media_id, mime_type)
        if result.cdn_url:
            message_data.setdefault('image', {})['_cdn_url'] = result.cdn_url
    except MediaRateLimitError as exc:
        logger.warning(
            "Image download rate-limited media_id=%s — retry %d/%d",
            media_id, self.request.retries + 1, self.max_retries,
        )
        raise self.retry(exc=exc)
    except MediaError as exc:
        logger.error(
            "Image download failed media_id=%s phone=%s: %s — routing without S3 URL",
            media_id, phone, exc,
        )

    _safe_route(message_data, phone, phone_number_id)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, ignore_result=True)
def process_document_task(
    self,
    media_id: str,
    mime_type: str,
    phone: str,
    phone_number_id: str,
    filename: str,
    caption: str,
    message_data: dict,
):
    """
    OCR pipeline for inbound documents (PDF / image-as-document).

    Archives to S3 when configured, then routes through MessageRouter for
    vision-LLM extraction and DocumentScan persistence.
    """
    from apps.whatsapp.media_services import (
        WhatsAppMediaDownloader,
        MediaRateLimitError,
        MediaError,
    )

    try:
        result = WhatsAppMediaDownloader.download(media_id, mime_type)
        if result.cdn_url:
            message_data.setdefault('document', {})['_cdn_url'] = result.cdn_url
    except MediaRateLimitError as exc:
        logger.warning(
            "Document download rate-limited media_id=%s — retry %d/%d",
            media_id, self.request.retries + 1, self.max_retries,
        )
        raise self.retry(exc=exc)
    except MediaError as exc:
        logger.error(
            "Document download failed media_id=%s phone=%s: %s",
            media_id, phone, exc,
        )

    _safe_route(message_data, phone, phone_number_id)


def _safe_route(message_data: dict, phone: str, phone_number_id: str) -> None:
    """Call MessageRouter.route, logging any crash without re-raising."""
    try:
        from apps.whatsapp.router import MessageRouter
        MessageRouter.route(message_data, phone, phone_number_id)
    except Exception:
        logger.exception(
            "MessageRouter crashed in media worker phone=%s type=%s",
            phone, message_data.get('type', '?'),
        )


# ── Scheduled health checks ────────────────────────────────────────────────────

@shared_task
def check_whatsapp_token_health():
    """
    Runs every 6 hours. Verifies the stored wa_access_token by calling the
    WhatsApp phone number info endpoint. Logs CRITICAL and creates admin
    notifications if the token is invalid or the phone number is unreachable.
    """
    from apps.config.services import SystemConfigService

    token    = SystemConfigService.get('wa_access_token')
    phone_id = SystemConfigService.get('wa_phone_number_id')

    if not token or not phone_id:
        logger.warning(
            "check_whatsapp_token_health: wa_access_token or wa_phone_number_id not "
            "configured in SystemConfig — skipping health check."
        )
        return 'unconfigured'

    try:
        r = requests.get(
            f"{WA_API_URL}/{phone_id}",
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )

        if r.status_code == 401:
            logger.critical(
                "WhatsApp token is INVALID or EXPIRED. "
                "Update wa_access_token in SystemConfig immediately."
            )
            _alert_admins(
                title='WhatsApp Token Expired',
                message=(
                    '🚨 *WhatsApp token is invalid or expired.*\n\n'
                    'Bot messaging is offline. Update `wa_access_token` in '
                    'System Setup → WhatsApp API immediately.'
                ),
            )
            return 'invalid_token'

        r.raise_for_status()
        logger.info("check_whatsapp_token_health: OK (phone_id=%s)", phone_id)
        return 'ok'

    except requests.exceptions.Timeout:
        logger.error("check_whatsapp_token_health: request timed out")
        return 'timeout'
    except requests.exceptions.RequestException as exc:
        logger.error("check_whatsapp_token_health: request failed: %s", exc)
        return 'error'


def _alert_admins(title: str, message: str):
    try:
        from django.contrib.auth import get_user_model
        from apps.notifications.services import notify_user
        User = get_user_model()
        for admin in User.objects.filter(role='admin', is_active=True):
            try:
                notify_user(admin, title=title, message=message)
            except Exception as exc:
                logger.warning("_alert_admins: could not notify admin %s: %s", admin.pk, exc)
    except Exception as exc:
        logger.error("_alert_admins: %s", exc)
