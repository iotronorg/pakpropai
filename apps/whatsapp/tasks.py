import logging
import requests

from celery import shared_task

from apps.whatsapp.client import WA_API_URL

logger = logging.getLogger(__name__)


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
