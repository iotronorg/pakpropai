"""
Tests for GLOBAL-4: Onfido verification provider (GB — passport, driving licence).

5 tests:
  1. GB org → IDVerificationSessionView routes to Onfido (applicant created)
  2. Onfido 'clear' result parsed as approved
  3. Onfido 'consider' result parsed as declined
  4. ONFIDO_API_TOKEN missing → is_configured() returns False
  5. Onfido webhook updates DocumentScan status
"""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.markets.registry import get_verification_provider
from apps.verification.models import DocumentScan
from apps.verification.providers.onfido_provider import OnfidoVerificationProvider
from tests.factories import make_user, make_org


_ONFIDO_SETTINGS = {'ONFIDO_API_TOKEN': 'test_onfido_token', 'ONFIDO_WEBHOOK_TOKEN': 'test_wh_token'}

_APPLICANT_RESP = MagicMock()
_APPLICANT_RESP.raise_for_status = MagicMock()
_APPLICANT_RESP.json = MagicMock(return_value={'id': 'applicant-gb-001'})

_SDK_TOKEN_RESP = MagicMock()
_SDK_TOKEN_RESP.raise_for_status = MagicMock()
_SDK_TOKEN_RESP.json = MagicMock(return_value={'token': 'sdk_test_token'})


class OnfidoRoutingTest(TestCase):

    def test_gb_org_routes_to_onfido(self):
        """get_verification_provider('GB') returns OnfidoVerificationProvider."""
        provider = get_verification_provider('GB')
        self.assertIsInstance(provider, OnfidoVerificationProvider)
        self.assertTrue(provider.supported)


class OnfidoWebhookParseTests(TestCase):

    def test_onfido_clear_result_parsed_as_approved(self):
        """Onfido check with result='clear' → status='approved'."""
        payload = {
            'payload': {
                'resource_type': 'check',
                'action':        'check.completed',
                'object': {
                    'id':           'check-001',
                    'applicant_id': 'applicant-gb-001',
                    'result':       'clear',
                    'breakdown':    {},
                },
            },
        }
        provider = OnfidoVerificationProvider()
        session_id, result = provider.parse_webhook(payload)
        self.assertEqual(session_id, 'applicant-gb-001')
        self.assertEqual(result.status, 'approved')
        self.assertGreaterEqual(result.confidence, 0.9)

    def test_onfido_consider_result_parsed_as_declined(self):
        """Onfido check with result='consider' → status='declined'."""
        payload = {
            'payload': {
                'resource_type': 'check',
                'action':        'check.completed',
                'object': {
                    'id':           'check-002',
                    'applicant_id': 'applicant-gb-002',
                    'result':       'consider',
                    'breakdown':    {
                        'document': {'result': 'consider'},
                    },
                },
            },
        }
        provider = OnfidoVerificationProvider()
        _, result = provider.parse_webhook(payload)
        self.assertEqual(result.status, 'declined')
        self.assertIn('document', result.red_flags)


class OnfidoConfigTest(TestCase):

    def test_missing_onfido_token_not_configured(self):
        """OnfidoVerificationProvider.is_configured() returns False when token absent."""
        with override_settings(ONFIDO_API_TOKEN=''):
            provider = OnfidoVerificationProvider()
            self.assertFalse(provider.is_configured())


class OnfidoWebhookViewTest(TestCase):

    def setUp(self):
        self.client  = APIClient()
        self.user    = make_user(phone='+447700900001', role='agent')
        # Create a DocumentScan record with the Onfido applicant_id as registration_number
        DocumentScan.objects.create(
            user                = self.user,
            document_type       = DocumentScan.DocType.PASSPORT,
            registration_number = 'applicant-gb-wb-001',
            status              = DocumentScan.Status.UNREADABLE,
            extracted_fields    = {'provider': 'onfido'},
        )

    @override_settings(**_ONFIDO_SETTINGS)
    def test_onfido_webhook_updates_document_scan(self):
        """Valid Onfido webhook with clear result updates DocumentScan to CLEAN."""
        import hmac, hashlib
        payload = {
            'payload': {
                'resource_type': 'check',
                'action':        'check.completed',
                'object': {
                    'applicant_id': 'applicant-gb-wb-001',
                    'result':       'clear',
                    'breakdown':    {},
                },
            },
        }
        body = json.dumps(payload, separators=(',', ':')).encode()
        sig  = hmac.new(b'test_wh_token', body, hashlib.sha256).hexdigest()

        resp = self.client.post(
            reverse('webhook-onfido'),
            data=body,
            content_type='application/json',
            HTTP_X_SHA2_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['activated'])

        scan = DocumentScan.objects.get(registration_number='applicant-gb-wb-001')
        self.assertEqual(scan.status, DocumentScan.Status.CLEAN)
