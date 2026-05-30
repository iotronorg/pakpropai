"""
Tests for GLOBAL-3: Jumio verification provider (AE — Emirates ID).

6 tests:
  1. AE org routes to JumioVerificationProvider
  2. PK org routes to UnsupportedProvider (not Jumio)
  3. Jumio webhook result parsed correctly (approved)
  4. Jumio fail-open: session creation failure returns 502, not an unhandled exception
  5. get_verification_provider('XX') returns supported=False
  6. Missing JUMIO env vars → is_configured() returns False (no WARNING needed in test)
"""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.markets.registry import get_verification_provider
from apps.verification.providers.jumio_provider import JumioVerificationProvider
from apps.verification.providers.base import UnsupportedProvider
from tests.factories import make_user, make_org, make_property


class ProviderRoutingTests(TestCase):

    def test_ae_org_routes_to_jumio(self):
        """get_verification_provider('AE') returns JumioVerificationProvider."""
        provider = get_verification_provider('AE')
        self.assertIsInstance(provider, JumioVerificationProvider)
        self.assertTrue(provider.supported)

    def test_pk_org_routes_to_unsupported(self):
        """get_verification_provider('PK') returns UnsupportedProvider (internal OCR handles PK)."""
        provider = get_verification_provider('PK')
        self.assertIsInstance(provider, UnsupportedProvider)
        self.assertFalse(provider.supported)

    def test_unknown_country_returns_unsupported(self):
        """get_verification_provider('XX') returns supported=False."""
        provider = get_verification_provider('XX')
        self.assertFalse(provider.supported)
        self.assertIsInstance(provider, UnsupportedProvider)


class JumioWebhookParseTests(TestCase):

    def test_jumio_webhook_approved_parsed_correctly(self):
        """Jumio 'passed' decision → status='approved', confidence≥0.9."""
        payload = {
            'account': {'id': 'acc-001'},
            'workflowExecution': {
                'id': 'wf-001',
                'decision': {'type': 'PASSED'},
                'capabilities': {
                    'extraction': {
                        'data': {
                            'firstName': 'Ahmed',
                            'lastName':  'Al-Mansoori',
                            'idNumber':  '784-1990-1234567-1',
                        }
                    }
                },
            },
        }
        provider = JumioVerificationProvider()
        session_id, result = provider.parse_webhook(payload)

        self.assertEqual(session_id, 'acc-001::wf-001')
        self.assertEqual(result.status, 'approved')
        self.assertGreaterEqual(result.confidence, 0.9)
        self.assertEqual(result.extracted_fields.get('first_name'), 'Ahmed')

    def test_jumio_webhook_failed_parsed_correctly(self):
        """Jumio 'failed' decision → status='declined'."""
        payload = {
            'account': {'id': 'acc-002'},
            'workflowExecution': {
                'id':       'wf-002',
                'decision': {'type': 'FAILED'},
                'capabilities': {},
            },
        }
        provider = JumioVerificationProvider()
        _, result = provider.parse_webhook(payload)
        self.assertEqual(result.status, 'declined')
        self.assertEqual(result.confidence, 0.0)


class JumioSessionCreationTests(TestCase):

    def setUp(self):
        self.client   = APIClient()
        self.ae_org   = make_org(name='Dubai Realty', country='AE')
        self.dev_user = make_user(phone='+971501234567', role='developer')
        self.client.force_authenticate(user=self.dev_user)

    def test_jumio_create_session_fail_returns_502(self):
        """When Jumio API is down, IDVerificationSessionView returns 502, not 500."""
        with patch('requests.post', side_effect=Exception('Jumio unreachable')):
            with patch('apps.core.permissions.get_user_org', return_value=self.ae_org):
                from django.test.utils import override_settings
                with override_settings(JUMIO_API_TOKEN='tok', JUMIO_API_SECRET='sec'):
                    resp = self.client.post(reverse('id-verify'), {'doc_type': 'passport'})

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    def test_missing_jumio_env_vars_not_configured(self):
        """JumioVerificationProvider.is_configured() returns False when env vars absent."""
        from django.test.utils import override_settings
        with override_settings(JUMIO_API_TOKEN='', JUMIO_API_SECRET=''):
            provider = JumioVerificationProvider()
            self.assertFalse(provider.is_configured())
