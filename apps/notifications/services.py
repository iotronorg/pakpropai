import logging
from apps.whatsapp.client import WhatsAppClient

logger = logging.getLogger(__name__)


def send_whatsapp_otp(phone: str, code: str) -> None:
    body = f"Your PakProp verification code is: {code}\n\nThis code expires in 5 minutes."
    try:
        WhatsAppClient.send_text(phone, body)
    except Exception as exc:
        logger.error(f"OTP send failed for {phone}: {exc}")
        # Fallback to console so dev flow keeps working
        logger.warning(f"[OTP FALLBACK] {phone}: {code}")


def send_whatsapp_message(phone: str, body: str) -> None:
    WhatsAppClient.send_text(phone, body)