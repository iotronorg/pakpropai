import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def retry_failed_notifications():
    """
    Runs every 30 minutes. Re-queues FAILED notifications from the last 48h through
    the outbox, excluding ones that failed due to the WhatsApp 24h messaging window
    (those won't succeed on retry — the client must message first) or preferences.
    """
    from .delivery import enqueue_notification
    from .models import Notification

    cutoff = timezone.now() - timedelta(hours=48)
    qs = Notification.objects.filter(
        status=Notification.Status.FAILED,
        created_at__gte=cutoff,
    ).exclude(error__icontains='24').exclude(error__icontains='preferences')

    count = 0
    for n in qs:
        n.status = Notification.Status.PENDING
        n.save(update_fields=['status'])
        enqueue_notification(n, retry=True)
        count += 1

    logger.info(f"retry_failed_notifications: re-queued {count} notification(s)")
    return count


# ── Legacy task shims ─────────────────────────────────────────────────────────
# New code enqueues through apps.core.outbox (ADR-014). These remain so messages
# already sitting in the broker at deploy time are still processed.

@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def send_otp_async(self, phone: str, code: str):
    """Deprecated: deliver an OTP passed in the broker message. Never logs the code."""
    from apps.whatsapp.client import get_wa_client
    try:
        get_wa_client(None).send_otp(phone, code)
        logger.info("OTP delivered to %s (legacy task)", phone)
    except Exception as exc:
        logger.error("OTP delivery attempt %s failed for %s: %s", self.request.retries + 1, phone, exc)
        raise self.retry(exc=exc)


@shared_task
def send_whatsapp_async(notification_id: str):
    """Deprecated: route a notification id through the outbox and deliver it now."""
    from apps.core import outbox
    from .delivery import enqueue_notification
    from .models import Notification

    try:
        n = Notification.objects.get(id=notification_id)
    except (Notification.DoesNotExist, ValueError):
        return None
    msg = enqueue_notification(n)
    return outbox.dispatch(msg.pk)
