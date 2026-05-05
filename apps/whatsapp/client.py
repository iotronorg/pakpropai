import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

WA_API_URL = "https://graph.facebook.com/v20.0"


class WhatsAppClient:

    @classmethod
    def _headers(cls):
        return {
            'Authorization': f'Bearer {settings.WA_ACCESS_TOKEN}',
            'Content-Type':  'application/json',
        }

    @classmethod
    def _phone_url(cls):
        return f"{WA_API_URL}/{settings.WA_PHONE_NUMBER_ID}/messages"

    @classmethod
    def send_text(cls, phone: str, body: str) -> dict:
        # Meta expects the phone WITHOUT the leading "+"
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
    def download_media(cls, media_id: str) -> bytes:
        # Step 1: get the media URL
        info = requests.get(
            f"{WA_API_URL}/{media_id}",
            headers={'Authorization': f'Bearer {settings.WA_ACCESS_TOKEN}'},
            timeout=10,
        )
        info.raise_for_status()
        media_url = info.json()['url']

        # Step 2: download the bytes (auth required)
        media = requests.get(
            media_url,
            headers={'Authorization': f'Bearer {settings.WA_ACCESS_TOKEN}'},
            timeout=30,
        )
        media.raise_for_status()
        return media.content