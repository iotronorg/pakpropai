from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class VoiceProviderConfig:
    provider:         str
    account_sid:      str
    auth_token:       str
    phone_number:     str
    webhook_base_url: str


@dataclass
class VoiceCallResult:
    call_sid: str
    status:   str
    error:    Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


class VoiceProvider(ABC):

    @abstractmethod
    def initiate_outbound(self, to_phone: str, from_phone: str, org_id: str) -> VoiceCallResult:
        ...

    @abstractmethod
    def generate_twiml_answer(self, call_sid: str, stream_url: str) -> str:
        """Return TwiML XML string routing inbound audio to the WS stream URL."""
        ...

    @abstractmethod
    def inject_voice_reply(self, call_sid: str, text: str, voice: str = 'Polly.Joanna') -> bool:
        """Interrupt the current call and speak `text` via Twilio TTS (<Say> verb)."""
        ...

    @abstractmethod
    def redirect_to_agent(self, call_sid: str) -> bool:
        """Switch call from AI stream to a waiting agent (puts call on hold / dial agent)."""
        ...

    @abstractmethod
    def hangup(self, call_sid: str) -> bool:
        ...

    @abstractmethod
    def validate_webhook_signature(self, url: str, params: dict, signature: str) -> bool:
        ...


def get_org_provider(org) -> VoiceProvider:
    """Return the configured VoiceProvider for an org, falling back to platform SystemConfig."""
    from apps.config.services import SystemConfigService
    try:
        cfg = org.voice_config
        account_sid  = cfg.account_sid  or SystemConfigService.get('twilio_account_sid', '')
        auth_token   = cfg.auth_token   or SystemConfigService.get('twilio_auth_token',  '')
        phone_number = cfg.phone_number or SystemConfigService.get('twilio_phone_number', '')
        voice_name   = cfg.ai_voice_name
    except Exception:
        account_sid  = SystemConfigService.get('twilio_account_sid', '')
        auth_token   = SystemConfigService.get('twilio_auth_token',  '')
        phone_number = SystemConfigService.get('twilio_phone_number', '')
        voice_name   = 'Polly.Joanna'

    from django.conf import settings
    base_url = getattr(settings, 'BASE_URL', 'https://example.com')

    config = VoiceProviderConfig(
        provider='twilio',
        account_sid=account_sid,
        auth_token=auth_token,
        phone_number=phone_number,
        webhook_base_url=base_url,
    )
    from .twilio_provider import TwilioVoiceProvider
    return TwilioVoiceProvider(config, voice_name=voice_name)
