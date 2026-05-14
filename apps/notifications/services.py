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


_EVENT_PREF_FIELD = {
    'lead_updates':          'lead_updates',
    'appointment_reminders': 'appointment_reminders',
    'deal_updates':          'deal_updates',
    'report_ready':          'report_ready',
    'marketing':             'marketing',
}


def notify_user(
    user,
    title: str,
    message: str,
    send_whatsapp: bool = True,
    event_type: str = 'lead_updates',
) -> None:
    """Create a Notification record and deliver via WhatsApp if user preferences allow it."""
    try:
        from .models import Notification, UserNotificationPreference

        prefs, _ = UserNotificationPreference.objects.get_or_create(user=user)

        # Resolve whether WhatsApp delivery is permitted by user preferences
        channel_allowed = prefs.whatsapp_enabled
        event_field = _EVENT_PREF_FIELD.get(event_type, 'lead_updates')
        event_allowed = getattr(prefs, event_field, True)

        n = Notification.objects.create(
            user=user,
            title=title,
            message=message,
            channel=Notification.Channel.WHATSAPP,
        )

        if send_whatsapp and channel_allowed and event_allowed:
            from .tasks import send_whatsapp_async
            send_whatsapp_async.delay(str(n.id))
        elif send_whatsapp and not (channel_allowed and event_allowed):
            n.status = Notification.Status.FAILED
            n.error  = 'Blocked by user notification preferences'
            n.save(update_fields=['status', 'error'])
            logger.debug(
                f"notify_user: delivery skipped for {getattr(user, 'phone', '?')} "
                f"(channel_allowed={channel_allowed}, event={event_type}={event_allowed})"
            )
    except Exception as exc:
        logger.warning(f"notify_user failed for {getattr(user, 'phone', '?')}: {exc}")
