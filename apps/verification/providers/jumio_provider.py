"""
JumioVerificationProvider — Emirates ID and passport verification for AE market.

API docs: https://api.jumio.com/api/v1/
Workflow 10009 = ID + liveness (Emirates ID, passport, driving licence)
"""
import base64
import logging
from typing import Optional

import requests
from django.conf import settings

from .base import VerificationProvider, VerificationResult, VerificationSession

logger = logging.getLogger(__name__)

_JUMIO_API_BASE = 'https://api.jumio.com'
_WORKFLOW_KEY   = 10009   # ID capture + liveness


class JumioVerificationProvider(VerificationProvider):
    name      = 'jumio'
    supported = True

    def is_configured(self) -> bool:
        return bool(
            getattr(settings, 'JUMIO_API_TOKEN',  '') and
            getattr(settings, 'JUMIO_API_SECRET', '')
        )

    def _auth_headers(self) -> dict:
        token  = getattr(settings, 'JUMIO_API_TOKEN',  '')
        secret = getattr(settings, 'JUMIO_API_SECRET', '')
        creds  = base64.b64encode(f'{token}:{secret}'.encode()).decode()
        return {'Authorization': f'Basic {creds}', 'Content-Type': 'application/json'}

    def create_session(
        self,
        user_id:      str,
        doc_type:     str = 'passport',
        redirect_url: str = '',
    ) -> VerificationSession:
        """
        1. Create Jumio account for user_id.
        2. Start workflow-execution (hosted web flow).
        Returns session_url to redirect the user to.
        """
        if not self.is_configured():
            raise ValueError('JUMIO_API_TOKEN and JUMIO_API_SECRET must be set')

        # Step 1 — create / retrieve account
        account_resp = requests.post(
            f'{_JUMIO_API_BASE}/api/v1/accounts',
            json={'customerInternalReference': user_id},
            headers=self._auth_headers(),
            timeout=15,
        )
        account_resp.raise_for_status()
        account_id = account_resp.json()['account']['id']

        # Step 2 — start workflow execution
        callback_url = getattr(settings, 'JUMIO_CALLBACK_URL', '') or redirect_url
        wf_resp = requests.post(
            f'{_JUMIO_API_BASE}/api/v1/accounts/{account_id}/workflow-executions',
            json={
                'workflowDefinition': {'key': _WORKFLOW_KEY},
                'customerInternalReference': user_id,
                'callbackUrl': callback_url,
                'userReference': user_id,
            },
            headers=self._auth_headers(),
            timeout=15,
        )
        wf_resp.raise_for_status()
        wf_data = wf_resp.json()
        wf_id       = wf_data['workflowExecution']['id']
        session_url = wf_data['workflowExecution']['web']['href']

        return VerificationSession(
            session_id  = f'{account_id}::{wf_id}',
            session_url = session_url,
            provider    = self.name,
        )

    def poll_result(self, session_id: str) -> Optional[VerificationResult]:
        """
        Poll the Jumio GET endpoint for a completed workflow result.
        Returns None if still pending.
        """
        if '::' not in session_id:
            return None
        account_id, wf_id = session_id.split('::', 1)

        if not self.is_configured():
            return None

        resp = requests.get(
            f'{_JUMIO_API_BASE}/api/v1/accounts/{account_id}/workflow-executions/{wf_id}',
            headers=self._auth_headers(),
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._parse_workflow_data(resp.json())

    def parse_webhook(self, payload: dict) -> tuple[str, VerificationResult]:
        """Parse a Jumio callback webhook body."""
        account_id = payload.get('account', {}).get('id', '')
        wf         = payload.get('workflowExecution', {})
        wf_id      = wf.get('id', '')
        session_id = f'{account_id}::{wf_id}'
        return session_id, self._parse_workflow_data(payload)

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_workflow_data(data: dict) -> VerificationResult:
        wf           = data.get('workflowExecution', data)
        decision     = wf.get('decision', {})
        decision_type = decision.get('type', '').lower()
        capabilities  = wf.get('capabilities', {})

        if decision_type == 'passed':
            final_status = 'approved'
            confidence   = 0.95
        elif decision_type == 'failed':
            final_status = 'declined'
            confidence   = 0.0
        elif decision_type == 'warning':
            final_status = 'declined'
            confidence   = 0.3
        else:
            final_status = 'pending'
            confidence   = 0.5

        # Try to extract identity fields from extraction capability
        extraction_data = (
            capabilities.get('extraction', {}).get('data', {}) or {}
        )
        extracted = {}
        if extraction_data:
            extracted = {
                'first_name':      extraction_data.get('firstName', ''),
                'last_name':       extraction_data.get('lastName', ''),
                'document_number': extraction_data.get('idNumber', ''),
                'expiry_date':     extraction_data.get('expiryDate', ''),
                'date_of_birth':   extraction_data.get('dateOfBirth', ''),
            }

        red_flags = [
            r.get('code', '')
            for r in decision.get('details', {}).get('reasons', [])
            if r.get('code')
        ]

        return VerificationResult(
            status           = final_status,
            confidence       = confidence,
            extracted_fields = extracted,
            red_flags        = red_flags,
        )
