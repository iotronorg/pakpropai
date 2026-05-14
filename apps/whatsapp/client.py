import logging
import requests
from datetime import timedelta
from django.utils import timezone

logger = logging.getLogger(__name__)

WA_API_URL = "https://graph.facebook.com/v20.0"
_24H = timedelta(hours=24)


def is_within_24h_window(phone: str) -> bool:
    """
    Returns True if the user has sent us an inbound message in the last 24 hours.
    WhatsApp only allows free-form outbound messages within this window.
    """
    from .models import WhatsAppSession
    normalized = phone.lstrip('+')
    try:
        session = WhatsAppSession.objects.filter(phone=normalized).order_by('-last_message_at').first()
        if not session:
            return False
        # Only count the window if there's been a recent INBOUND message
        cutoff = timezone.now() - _24H
        return session.messages.filter(
            direction='inbound',
            created_at__gte=cutoff,
        ).exists()
    except Exception:
        return False


def _wa_token() -> str:
    from apps.config.services import SystemConfigService
    return SystemConfigService.get('wa_access_token')


def _wa_phone_id() -> str:
    from apps.config.services import SystemConfigService
    return SystemConfigService.get('wa_phone_number_id')


class WhatsAppClient:

    @classmethod
    def _headers(cls):
        return {
            'Authorization': f'Bearer {_wa_token()}',
            'Content-Type':  'application/json',
        }

    @classmethod
    def _phone_url(cls):
        return f"{WA_API_URL}/{_wa_phone_id()}/messages"

    @classmethod
    def send_text(cls, phone: str, body: str, skip_window_check: bool = False) -> dict:
        """
        Send a free-form text message.
        Raises ValueError if the 24-hour customer-service window has closed
        (unless skip_window_check=True, which is only for template-triggered flows).
        """
        if not skip_window_check and not is_within_24h_window(phone):
            raise ValueError(
                f"Cannot send free-form message to {phone}: outside 24-hour WhatsApp window. "
                "Use send_template() with a pre-approved template instead."
            )
        to = phone.lstrip('+')
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type':    'individual',
            'to':                to,
            'type':              'text',
            'text':              {'preview_url': False, 'body': body[:4096]},
        }
        try:
            r = requests.post(cls._phone_url(), headers=cls._headers(), json=payload, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as exc:
            logger.error(f"WA send failed to {phone}: {exc} — body={getattr(exc.response, 'text', '')}")
            raise

    @classmethod
    def send_otp(cls, phone: str, code: str) -> dict:
        """
        Send an OTP via template (required for first-contact users outside the 24-h window).
        Falls back to free-text when wa_otp_template_name is not configured (dev only).
        """
        from apps.config.services import SystemConfigService
        template_name = SystemConfigService.get('wa_otp_template_name')
        if template_name:
            components = [
                {
                    'type': 'body',
                    'parameters': [{'type': 'text', 'text': code}],
                }
            ]
            return cls.send_template(phone, template_name, components=components)
        # Dev fallback — only works if the user has messaged the bot in the last 24 h
        body = f"Your PakProp AI verification code is: *{code}*\n\nExpires in 5 minutes. Do not share."
        return cls.send_text(phone, body)

    @classmethod
    def send_template(cls, phone: str, template_name: str, language: str = 'en_US',
                      components: list = None) -> dict:
        to = phone.lstrip('+')
        payload = {
            'messaging_product': 'whatsapp',
            'to':                to,
            'type':              'template',
            'template': {
                'name': template_name,
                'language': {'code': language},
                'components': components or [],
            },
        }
        r = requests.post(cls._phone_url(), headers=cls._headers(), json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    @classmethod
    def mark_read(cls, message_id: str) -> None:
        """Send a read receipt for an inbound message (shows blue ticks to the sender)."""
        try:
            requests.post(
                cls._phone_url(),
                headers=cls._headers(),
                json={
                    'messaging_product': 'whatsapp',
                    'status': 'read',
                    'message_id': message_id,
                },
                timeout=5,
            )
        except Exception:
            pass  # Non-critical; never block message processing

    @classmethod
    def download_media(cls, media_id: str) -> bytes:
        token = _wa_token()
        # Step 1: get the media URL
        info = requests.get(
            f"{WA_API_URL}/{media_id}",
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )
        info.raise_for_status()
        media_url = info.json()['url']

        # Step 2: download the bytes (auth required)
        media = requests.get(
            media_url,
            headers={'Authorization': f'Bearer {token}'},
            timeout=30,
        )
        media.raise_for_status()
        return media.content