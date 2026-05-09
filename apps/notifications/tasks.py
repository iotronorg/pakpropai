import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def retry_failed_notifications():
    """
    Runs every 30 minutes. Re-queues FAILED notifications from the last 48h,
    excluding ones that failed due to the WhatsApp 24h messaging window
    (those won't succeed on retry — the client must message first).
    """
    from .models import Notification

    cutoff = timezone.now() - timedelta(hours=48)
    qs = Notification.objects.filter(
        status=Notification.Status.FAILED,
        created_at__gte=cutoff,
    ).exclude(error__icontains='24')

    count = 0
    for n in qs:
        n.status = Notification.Status.PENDING
        n.save(update_fields=['status'])
        send_whatsapp_async.delay(str(n.id))
        count += 1

    logger.info(f"retry_failed_notifications: re-queued {count} notification(s)")
    return count


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
    except ValueError as exc:
        # 24h window expired — retrying won't help; mark as skipped
        n.status = Notification.Status.FAILED
        n.error  = str(exc)[:1000]
        n.save()
        logger.warning(f"WA 24h window expired for notification {notification_id}: {exc}")
    except Exception as exc:
        n.status = Notification.Status.FAILED
        n.error  = str(exc)[:1000]
        n.save()
        raise self.retry(exc=exc)