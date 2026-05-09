import logging
from apps.whatsapp.client import WhatsAppClient

logger = logging.getLogger(__name__)


def send_whatsapp_otp(phone: str, code: str) -> None:
    try:
        WhatsAppClient.send_otp(phone, code)
    except Exception as exc:
        logger.error(f"OTP WhatsApp send failed for {phone}: {exc}")
        logger.warning(f"[OTP FALLBACK] {phone}: {code}")
        raise


def send_whatsapp_message(phone: str, body: str) -> None:
    WhatsAppClient.send_text(phone, body)


def notify_user(user, title: str, message: str, send_whatsapp: bool = True) -> None:
    """Create a Notification record and optionally deliver via WhatsApp async."""
    try:
        from .models import Notification
        n = Notification.objects.create(
            user=user,
            title=title,
            message=message,
            channel=Notification.Channel.WHATSAPP,
        )
        if send_whatsapp:
            from .tasks import send_whatsapp_async
            send_whatsapp_async.delay(str(n.id))
    except Exception as exc:
        logger.warning(f"notify_user failed for {getattr(user, 'phone', '?')}: {exc}")
