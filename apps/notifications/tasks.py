import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def send_otp_async(self, phone: str, code: str):
    """Deliver an OTP via WhatsApp. Retries up to 3× with 15-second backoff."""
    from apps.whatsapp.client import WhatsAppClient
    try:
        WhatsAppClient.send_otp(phone, code)
        logger.info(f"OTP delivered to {phone}")
    except Exception as exc:
        logger.error(f"OTP delivery attempt {self.request.retries + 1} failed for {phone}: {exc}")
        logger.warning(f"[OTP FALLBACK] {phone}: {code}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_whatsapp_async(self, notification_id: str):
    from .models import Notification
    from apps.whatsapp.client import WhatsAppClient

    try:
        n = Notification.objects.get(id=notification_id)
    except Notification.DoesNotExist:
        return

    try:
        resp = WhatsAppClient.send_text(n.user.phone, n.message)
        n.status        = Notification.Status.SENT
        n.wa_message_id = resp.get('messages', [{}])[0].get('id', '')
        n.sent_at       = timezone.now()
        n.save()
    except Exception as exc:
        n.status = Notification.Status.FAILED
        n.error  = str(exc)[:1000]
        n.save()
        raise self.retry(exc=exc)