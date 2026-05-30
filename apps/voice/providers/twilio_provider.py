import logging
from .base import VoiceProvider, VoiceProviderConfig, VoiceCallResult

logger = logging.getLogger(__name__)


class TwilioVoiceProvider(VoiceProvider):

    def __init__(self, config: VoiceProviderConfig, voice_name: str = 'Polly.Joanna'):
        self.config     = config
        self.voice_name = voice_name
        self._client    = None  # lazy — avoids import error when Twilio not configured

    def _get_client(self):
        if self._client is None:
            from twilio.rest import Client
            self._client = Client(self.config.account_sid, self.config.auth_token)
        return self._client

    def initiate_outbound(self, to_phone: str, from_phone: str, org_id: str) -> VoiceCallResult:
        base = self.config.webhook_base_url
        try:
            client = self._get_client()
            stream_url = f'wss://{base.replace("https://", "").replace("http://", "")}/ws/voice/pending/'
            call = client.calls.create(
                to=to_phone,
                from_=from_phone or self.config.phone_number,
                twiml=self.generate_twiml_answer('pending', stream_url),
                status_callback=f'{base}/api/v1/voice/webhook/status/',
                status_callback_method='POST',
            )
            return VoiceCallResult(call_sid=call.sid, status=call.status)
        except Exception as exc:
            logger.error("TwilioVoiceProvider.initiate_outbound failed: %s", exc)
            return VoiceCallResult(call_sid='', status='failed', error=str(exc))

    def generate_twiml_answer(self, call_sid: str, stream_url: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response>'
            f'<Connect><Stream url="{stream_url}"/></Connect>'
            '</Response>'
        )

    def inject_voice_reply(self, call_sid: str, text: str, voice: str = '') -> bool:
        """Push a <Say> TwiML update to the live call — Twilio reads the text aloud."""
        voice = voice or self.voice_name
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response>'
            f'<Say voice="{voice}">{text}</Say>'
            '<Pause length="1"/>'
            '</Response>'
        )
        try:
            self._get_client().calls(call_sid).update(twiml=twiml)
            return True
        except Exception as exc:
            logger.warning("inject_voice_reply failed call_sid=%s: %s", call_sid, exc)
            return False

    def redirect_to_agent(self, call_sid: str) -> bool:
        """Put the call into a hold state — agent calls back or joins via dashboard."""
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response>'
            '<Say>Please hold. A team member will join momentarily.</Say>'
            '<Pause length="60"/>'
            '</Response>'
        )
        try:
            self._get_client().calls(call_sid).update(twiml=twiml)
            return True
        except Exception as exc:
            logger.warning("redirect_to_agent failed call_sid=%s: %s", call_sid, exc)
            return False

    def hangup(self, call_sid: str) -> bool:
        try:
            self._get_client().calls(call_sid).update(status='completed')
            return True
        except Exception as exc:
            logger.warning("hangup failed call_sid=%s: %s", call_sid, exc)
            return False

    def validate_webhook_signature(self, url: str, params: dict, signature: str) -> bool:
        if not self.config.auth_token:
            return False
        try:
            from twilio.request_validator import RequestValidator
            return RequestValidator(self.config.auth_token).validate(url, params, signature)
        except Exception as exc:
            logger.warning("Twilio signature validation error: %s", exc)
            return False
