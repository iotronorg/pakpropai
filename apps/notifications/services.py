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