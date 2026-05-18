import logging
import requests

from celery import shared_task

from apps.whatsapp.client import WA_API_URL

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def process_incoming_whatsapp_task(message: dict, phone_number_id: str = ''):
    """
    Async Celery worker for a single inbound WhatsApp message.

    The webhook view returns 200 OK immediately after dispatching this task.
    All message processing — mark-read, routing, AI, media handling — happens
    here so the webhook never blocks.

    Retries are intentionally disabled: the idempotency key is set in the view
    before dispatch, so a Celery retry would silently drop the message. If
    processing fails, the error is logged and the message is permanently consumed.
    """
    msg_id = message.get('id', '')
    phone  = message.get('from', '')

    if not msg_id or not phone:
        logger.warning("process_incoming_whatsapp_task: missing id or from — %r", message)
        return

    # Show blue ticks immediately before the AI thinks (3-15 s).
    try:
        from apps.whatsapp.client import WhatsAppClient
        WhatsAppClient.mark_read(msg_id)
    except Exception:
        pass  # Non-critical — never block message processing

    msg_type = message.get('type', 'text')

    # ── Stub routing table for media types ────────────────────────────────────
    # Each branch logs receipt and delegates to the MessageRouter.
    # Future: replace stubs with dedicated Celery tasks (transcribe_audio_task,
    # process_document_task, process_image_task) that can run in separate queues
    # with different resource profiles (GPU workers for STT/OCR, etc.).
    if msg_type == 'audio':
        audio_id = message.get('audio', {}).get('id', '')
        mime     = message.get('audio', {}).get('mime_type', 'audio/ogg')
        logger.info(
            "WA inbound audio phone=%s msg_id=%s media_id=%s mime=%s — routing to STT",
            phone, msg_id, audio_id, mime,
        )
        # TODO: dispatch to dedicated transcription worker once Whisper/STT is integrated.
        # For now: fall through to MessageRouter which handles transcription synchronously.

    elif msg_type == 'image':
        image_id = message.get('image', {}).get('id', '')
        mime     = message.get('image', {}).get('mime_type', 'image/jpeg')
        logger.info(
            "WA inbound image phone=%s msg_id=%s media_id=%s mime=%s — routing to Vision/OCR",
            phone, msg_id, image_id, mime,
        )
        # TODO: dispatch to dedicated OCR/Vision worker once computer vision pipeline is integrated.

    elif msg_type == 'document':
        doc_id   = message.get('document', {}).get('id', '')
        mime     = message.get('document', {}).get('mime_type', 'application/pdf')
        filename = message.get('document', {}).get('filename', '')
        logger.info(
            "WA inbound document phone=%s msg_id=%s media_id=%s mime=%s filename=%s — routing to OCR",
            phone, msg_id, doc_id, mime, filename,
        )
        # TODO: dispatch to dedicated document OCR worker (Tesseract/Vision) for async processing.

    # ── Route through MessageRouter (handles all types) ───────────────────────
    try:
        from apps.whatsapp.router import MessageRouter
        MessageRouter.route(message, phone, phone_number_id)
    except Exception:
        logger.exception(
            "MessageRouter crashed for phone=%s msg_id=%s type=%s",
            phone, msg_id, msg_type,
        )


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
        logger.info(f"check_whatsapp_token_health: OK (phone_id={phone_id})")
        return 'ok'

    except requests.exceptions.Timeout:
        logger.error("check_whatsapp_token_health: request timed out")
        return 'timeout'
    except requests.exceptions.RequestException as exc:
        logger.error(f"check_whatsapp_token_health: request failed: {exc}")
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
                logger.warning(f"_alert_admins: could not notify admin {admin.pk}: {exc}")
    except Exception as exc:
        logger.error(f"_alert_admins: {exc}")
