"""
OnfidoVerificationProvider — passport and driving-licence verification for GB market.

API docs: https://documentation.onfido.com/
"""
import hashlib
import hmac
import logging

import requests
from django.conf import settings

from .base import VerificationProvider, VerificationResult, VerificationSession

logger = logging.getLogger(__name__)

_ONFIDO_API_BASE = 'https://api.onfido.com/v3.6'


class OnfidoVerificationProvider(VerificationProvider):
    name      = 'onfido'
    supported = True

    def is_configured(self) -> bool:
        return bool(getattr(settings, 'ONFIDO_API_TOKEN', ''))

    def _headers(self) -> dict:
        return {
            'Authorization': f'Token token={getattr(settings, "ONFIDO_API_TOKEN", "")}',
            'Content-Type':  'application/json',
        }

    def create_session(
        self,
        user_id:      str,
        doc_type:     str = 'passport',
        redirect_url: str = '',
    ) -> VerificationSession:
        """
        1. Create Onfido applicant.
        2. Get SDK token for hosted web flow.
        Returns VerificationSession with session_url = hosted SDK URL.
        """
        if not self.is_configured():
            raise ValueError('ONFIDO_API_TOKEN must be set')

        # Step 1 — create applicant
        applicant_resp = requests.post(
            f'{_ONFIDO_API_BASE}/applicants',
            json={'external_id': user_id},
            headers=self._headers(),
            timeout=15,
        )
        applicant_resp.raise_for_status()
        applicant_id = applicant_resp.json()['id']

        # Step 2 — SDK token for hosted Onfido flow
        token_resp = requests.post(
            f'{_ONFIDO_API_BASE}/sdk_tokens',
            json={
                'applicant_id': applicant_id,
                'referrer':     redirect_url or '*',
            },
            headers=self._headers(),
            timeout=15,
        )
        token_resp.raise_for_status()
        sdk_token   = token_resp.json()['token']
        hosted_url  = f'https://sdk.onfido.com/?token={sdk_token}'

        return VerificationSession(
            session_id  = applicant_id,
            session_url = hosted_url,
            provider    = self.name,
        )

    def start_check(self, applicant_id: str) -> str:
        """Initiate an identity check after the user completes the hosted flow."""
        check_resp = requests.post(
            f'{_ONFIDO_API_BASE}/checks',
            json={
                'applicant_id': applicant_id,
                'report_names': ['document', 'facial_similarity_photo'],
            },
            headers=self._headers(),
            timeout=15,
        )
        check_resp.raise_for_status()
        return check_resp.json()['id']

    def verify_webhook_signature(self, payload_bytes: bytes, signature: str) -> bool:
        """Verify Onfido webhook HMAC-SHA256 signature."""
        token = getattr(settings, 'ONFIDO_WEBHOOK_TOKEN', '')
        if not token or not signature:
            return False
        expected = hmac.new(token.encode(), payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> tuple[str, VerificationResult]:
        """Parse Onfido check.completed webhook payload."""
        payload_obj   = payload.get('payload', {})
        obj           = payload_obj.get('object', {})
        session_id    = obj.get('applicant_id', '') or obj.get('id', '')
        result_str    = (obj.get('result') or '').lower()

        if result_str == 'clear':
            final_status = 'approved'
            confidence   = 0.95
        elif result_str in ('consider', 'suspected'):
            final_status = 'declined'
            confidence   = 0.2
        else:
            final_status = 'pending'
            confidence   = 0.5

        red_flags = [
            name
            for name, bdata in obj.get('breakdown', {}).items()
            if isinstance(bdata, dict) and bdata.get('result') not in ('clear', None)
        ]

        return session_id, VerificationResult(
            status           = final_status,
            confidence       = confidence,
            extracted_fields = {},
            red_flags        = red_flags[:5],
        )
