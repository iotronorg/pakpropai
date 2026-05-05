import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


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