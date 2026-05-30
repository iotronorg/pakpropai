"""
Tests for GLOBAL-9: UK Companies House agent verification.

4 tests:
  1. Valid CH number → is_active=True, company_name populated
  2. Invalid CH number → is_active=False, status='invalid'
  3. Non-GB org → endpoint returns skipped=True without calling CH API
  4. CH API down → endpoint returns 502, fail-open (no crash)
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.agents.services import CompaniesHouseVerifier
from tests.factories import make_user, make_org, make_agent

_VALID_CH_RESPONSE = {
    'company_name':   'SMITH PROPERTIES LIMITED',
    'company_number': '12345678',
    'company_status': 'active',
    'type':           'ltd',
}

_DISSOLVED_CH_RESPONSE = {
    'company_name':   'OLD FIRM LTD',
    'company_number': '99999999',
    'company_status': 'dissolved',
}


def _mock_ch(json_data=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = (status_code == 200)
    resp.json = MagicMock(return_value=json_data or {})
    return resp


class CompaniesHouseVerifierTests(TestCase):

    @patch('requests.get', return_value=_mock_ch(_VALID_CH_RESPONSE))
    def test_valid_number_returns_active(self, _mock):
        """Valid CH number → status='active', is_active=True, company_name set."""
        result = CompaniesHouseVerifier.verify('12345678')
        self.assertTrue(result['is_active'])
        self.assertEqual(result['status'], 'active')
        self.assertEqual(result['company_name'], 'SMITH PROPERTIES LIMITED')

    @patch('requests.get', return_value=_mock_ch(None, status_code=404))
    def test_invalid_number_returns_invalid(self, _mock):
        """Non-existent CH number (404) → status='invalid', is_active=False."""
        result = CompaniesHouseVerifier.verify('00000000')
        self.assertFalse(result['is_active'])
        self.assertEqual(result['status'], 'invalid')


class CompaniesHouseViewTests(TestCase):

    def setUp(self):
        self.client   = APIClient()
        self.gb_org   = make_org(name='London Estates', country='GB')
        self.ae_org   = make_org(name='Dubai Realty',   country='AE')
        self.gb_dev   = make_user(phone='+447700900001', role='developer')
        self.ae_dev   = make_user(phone='+971501234567', role='developer')

    def test_non_gb_org_skipped_without_ch_call(self):
        """Non-GB org → view returns skipped=True; Companies House API never called."""
        self.client.force_authenticate(user=self.ae_dev)

        with patch('apps.core.permissions.get_user_org', return_value=self.ae_org):
            with patch('requests.get') as mock_get:
                resp = self.client.post(
                    reverse('agents-verify-ch'),
                    {'companies_house_number': '12345678'},
                )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data.get('skipped'))
        mock_get.assert_not_called()

    @patch('requests.get', side_effect=Exception('CH API timeout'))
    def test_ch_api_down_returns_502(self, _mock):
        """Companies House API failure → 502, not an unhandled exception."""
        self.client.force_authenticate(user=self.gb_dev)

        with patch('apps.core.permissions.get_user_org', return_value=self.gb_org):
            resp = self.client.post(
                reverse('agents-verify-ch'),
                {'companies_house_number': '12345678'},
            )

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
