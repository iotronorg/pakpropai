"""
Agent services — Companies House verification for UK market.

API: https://api.company-information.service.gov.uk/company/{number}
Free, no authentication required for basic company lookup.
"""
import logging

import requests

logger = logging.getLogger(__name__)

_CH_API_BASE = 'https://api.company-information.service.gov.uk'
_FETCH_TIMEOUT = 10


class CompaniesHouseVerifier:
    """Verify a UK Companies House registration number."""

    @classmethod
    def verify(cls, number: str) -> dict:
        """
        Look up a Companies House registration number.
        Returns:
            {status: 'active'|'dissolved'|'invalid'|'error',
             company_name: str,
             is_active: bool}
        """
        number = (number or '').strip().upper()
        if not number or len(number) > 8:
            return {'status': 'invalid', 'company_name': '', 'is_active': False}

        try:
            resp = requests.get(
                f'{_CH_API_BASE}/company/{number}',
                timeout=_FETCH_TIMEOUT,
                headers={'Accept': 'application/json'},
            )
        except Exception as exc:
            logger.warning('CompaniesHouseVerifier: request failed for %s: %s', number, exc)
            return {'status': 'error', 'company_name': '', 'is_active': False}

        if resp.status_code == 404:
            return {'status': 'invalid', 'company_name': '', 'is_active': False}

        if not resp.ok:
            logger.warning('CompaniesHouseVerifier: unexpected %s for %s', resp.status_code, number)
            return {'status': 'error', 'company_name': '', 'is_active': False}

        data = resp.json()
        company_name = data.get('company_name', '')
        company_status = (data.get('company_status') or '').lower()
        is_active = company_status == 'active'

        return {
            'status':       'active' if is_active else company_status or 'dissolved',
            'company_name': company_name,
            'is_active':    is_active,
        }
