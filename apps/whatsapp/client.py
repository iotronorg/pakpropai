import logging
import requests
from datetime import timedelta
from django.utils import timezone

logger = logging.getLogger(__name__)

WA_API_URL = "https://graph.facebook.com/v20.0"
_24H = timedelta(hours=24)


def is_within_24h_window(phone: str) -> bool:
    """
    Returns True if the user sent an inbound message in the last 24 hours.
    WhatsApp only allows free-form outbound messages within this window.
    """
    from .models import WhatsAppSession
    normalized = phone.lstrip('+')
    try:
        session = WhatsAppSession.objects.filter(phone=normalized).order_by('-last_message_at').first()
        if not session:
            return False
        cutoff = timezone.now() - _24H
        return session.messages.filter(direction='inbound', created_at__gte=cutoff).exists()
    except Exception:
        return False


def _wa_token() -> str:
    from apps.config.services import SystemConfigService
    return SystemConfigService.get('wa_access_token')


def _wa_phone_id() -> str:
    from apps.config.services import SystemConfigService
    return SystemConfigService.get('wa_phone_number_id')


def get_wa_client(org=None) -> 'WhatsAppClient':
    """
    Return a WhatsAppClient configured with org credentials when available,
    falling back to the global SystemConfig credentials.

    Pass org=None (or an org with no active config) to use global creds.
    """
    if org is not None:
        from .models import OrgWhatsAppConfig
        try:
            cfg = org.whatsapp_config
            if cfg.is_active and cfg.access_token and cfg.phone_number_id:
                return WhatsAppClient(cfg.access_token, cfg.phone_number_id)
        except OrgWhatsAppConfig.DoesNotExist:
            pass
    return WhatsAppClient(_wa_token(), _wa_phone_id())


class WhatsAppClient:

    def __init__(self, access_token: str, phone_number_id: str):
        self._access_token    = access_token
        self._phone_number_id = phone_number_id

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self._access_token}',
            'Content-Type':  'application/json',
        }

    def _phone_url(self) -> str:
        return f"{WA_API_URL}/{self._phone_number_id}/messages"

    def send_text(self, phone: str, body: str, skip_window_check: bool = False) -> dict:
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
            r = requests.post(self._phone_url(), headers=self._headers(), json=payload, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as exc:
            logger.error(f"WA send failed to {phone}: {exc} — body={getattr(exc.response, 'text', '')}")
            raise

    def send_otp(self, phone: str, code: str) -> dict:
        """
        Send OTP via a pre-approved Meta template when configured; otherwise
        falls back to free-form text (bypassing the 24h window check).
        """
        from apps.config.services import SystemConfigService
        template_name    = SystemConfigService.get('wa_otp_template_name')
        default_placeholder = 'otp_verification'
        if template_name and template_name != default_placeholder:
            components = [
                {'type': 'body', 'parameters': [{'type': 'text', 'text': code}]}
            ]
            return self.send_template(phone, template_name, components=components)
        body = f"Your RealTron AI verification code is: *{code}*\n\nExpires in 5 minutes. Do not share."
        return self.send_text(phone, body, skip_window_check=True)

    def send_template(self, phone: str, template_name: str, language: str = 'en_US',
                      components: list = None) -> dict:
        to = phone.lstrip('+')
        payload = {
            'messaging_product': 'whatsapp',
            'to':                to,
            'type':              'template',
            'template': {
                'name':       template_name,
                'language':   {'code': language},
                'components': components or [],
            },
        }
        r = requests.post(self._phone_url(), headers=self._headers(), json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def mark_read(self, message_id: str) -> None:
        """Send a read receipt (shows blue ticks to sender). Never blocks processing."""
        try:
            requests.post(
                self._phone_url(),
                headers=self._headers(),
                json={
                    'messaging_product': 'whatsapp',
                    'status':     'read',
                    'message_id': message_id,
                },
                timeout=5,
            )
        except Exception:
            pass

    def download_media(self, media_id: str) -> bytes:
        """Download media bytes from WhatsApp CDN using this client's access token."""
        info = requests.get(
            f"{WA_API_URL}/{media_id}",
            headers={'Authorization': f'Bearer {self._access_token}'},
            timeout=10,
        )
        info.raise_for_status()
        media_url = info.json()['url']
        media = requests.get(
            media_url,
            headers={'Authorization': f'Bearer {self._access_token}'},
            timeout=30,
        )
        media.raise_for_status()
        return media.content

    def upload_media(self, pdf_bytes: bytes, filename: str) -> str:
        """
        Upload a PDF to the WhatsApp media library via multipart/form-data.
        Returns the media_id string. Raises requests.HTTPError on non-2xx.
        """
        url = f"{WA_API_URL}/{self._phone_number_id}/media"
        r = requests.post(
            url,
            headers={'Authorization': f'Bearer {self._access_token}'},
            files={'file': (filename, pdf_bytes, 'application/pdf')},
            data={'messaging_product': 'whatsapp', 'type': 'application/pdf'},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()['id']

    def send_document(
        self,
        phone: str,
        media_id: str,
        filename: str = 'Property_Audit_Report.pdf',
        caption: str = '',
    ) -> dict:
        """
        Send a document (by media_id) to a WhatsApp user.
        Raises ValueError if outside 24-hour window.
        Raises requests.HTTPError on API error.
        """
        if not is_within_24h_window(phone):
            raise ValueError(
                f"Cannot send document to {phone}: outside 24-hour WhatsApp window."
            )
        to = phone.lstrip('+')
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type':    'individual',
            'to':                to,
            'type':              'document',
            'document': {
                'id':       media_id,
                'filename': filename,
                'caption':  caption[:1024],
            },
        }
        r = requests.post(self._phone_url(), headers=self._headers(), json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
