from __future__ import annotations
import uuid
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_FOOTER_MARKER = '✅ Verified by RealTron AI'
_FOOTER_TEMPLATE = (
    "\n\n---\n"
    "✅ Verified by RealTron AI. Is your property safe? "
    "Click to check: {tracking_url}"
)


def _tracking_url(code: str) -> str:
    base = getattr(settings, 'BASE_URL', 'https://realtron.ai').rstrip('/')
    return f"{base}/public/verify/?ref={code}"


class ReferralLinkGenerator:

    @staticmethod
    def get_or_create(org, lead=None) -> 'ReferralLink':
        """Return a stable ReferralLink for this org+lead pair (idempotent)."""
        from .models import ReferralLink
        link, _ = ReferralLink.objects.get_or_create(
            org=org,
            lead=lead,
            defaults={'code': uuid.uuid4().hex[:32]},
        )
        return link


class VirtualHookInjector:

    @staticmethod
    def inject_footer(text: str, org, lead=None) -> str:
        """
        Append the viral footer to text.
        Idempotent — returns text unchanged if footer already present.
        """
        if not text or _FOOTER_MARKER in text:
            return text

        try:
            link = ReferralLinkGenerator.get_or_create(org, lead)
            url  = _tracking_url(link.code)
            return text + _FOOTER_TEMPLATE.format(tracking_url=url)
        except Exception as exc:
            logger.warning('VirtualHookInjector.inject_footer fail-open: %s', exc)
            return text
