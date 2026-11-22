import logging

from django.db import transaction

from apps.whatsapp.client import get_wa_client

logger = logging.getLogger(__name__)


def send_whatsapp_otp(phone: str, code: str) -> None:
    """Synchronous OTP send (global credentials). Never logs the code (KI-033)."""
    try:
        get_wa_client(None).send_otp(phone, code)
    except Exception as exc:
        logger.error("OTP WhatsApp send failed for %s: %s", phone, exc)
        raise


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

        deliver = send_whatsapp and channel_allowed and event_allowed
        with transaction.atomic():
            n = Notification.objects.create(
                user=user,
                title=title,
                message=message,
                channel=Notification.Channel.WHATSAPP,
            )
            if deliver:
                # Transactional outbox (ADR-014): the delivery commits with the row.
                from .delivery import enqueue_notification
                enqueue_notification(n)

        if send_whatsapp and not deliver:
            n.status = Notification.Status.FAILED
            n.error  = 'Blocked by user notification preferences'
            n.save(update_fields=['status', 'error'])
            logger.debug(
                f"notify_user: delivery skipped for {getattr(user, 'phone', '?')} "
                f"(channel_allowed={channel_allowed}, event={event_type}={event_allowed})"
            )
    except Exception as exc:
        logger.warning(f"notify_user failed for {getattr(user, 'phone', '?')}: {exc}")
