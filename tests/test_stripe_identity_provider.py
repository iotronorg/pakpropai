"""
Tests for GLOBAL-4: Stripe Identity verification provider (US — state ID, passport).

5 tests:
  1. US org routes to StripeIdentityProvider
  2. Stripe Identity session URL returned by IDVerificationSessionView
  3. identity.verification_session.verified webhook → DocumentScan updated
  4. STRIPE_SECRET_KEY missing → is_configured() returns False
  5. Invalid webhook signature → 400
"""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.markets.registry import get_verification_provider
from apps.verification.models import DocumentScan
from apps.verification.providers.stripe_identity_provider import StripeIdentityProvider
from tests.factories import make_user, make_org

_STRIPE_SETTINGS = {
    'STRIPE_SECRET_KEY':               'sk_test_fake',
    'STRIPE_IDENTITY_WEBHOOK_SECRET':  'whsec_identity_fake',
}


class StripeIdentityRoutingTest(TestCase):

    def test_us_org_routes_to_stripe_identity(self):
        """get_verification_provider('US') returns StripeIdentityProvider."""
        provider = get_verification_provider('US')
        self.assertIsInstance(provider, StripeIdentityProvider)
        self.assertTrue(provider.supported)


class StripeIdentitySessionTest(TestCase):

    def setUp(self):
        self.client   = APIClient()
        self.us_org   = make_org(name='NYC Realty', country='US')
        self.dev_user = make_user(phone='+12125550100', role='developer')
        self.client.force_authenticate(user=self.dev_user)

    @patch('stripe.identity.VerificationSession.create')
    @override_settings(**_STRIPE_SETTINGS)
    def test_stripe_identity_session_url_returned(self, mock_create):
        """IDVerificationSessionView returns session_url for US org."""
        mock_session      = MagicMock()
        mock_session.id   = 'vs_test_abc123'
        mock_session.url  = 'https://verify.stripe.com/start/vs_test_abc123'
        mock_create.return_value = mock_session

        with patch('apps.core.permissions.get_user_org', return_value=self.us_org):
            resp = self.client.post(reverse('id-verify'), {'doc_type': 'passport'})

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['provider'], 'stripe_identity')
        self.assertIn('verify.stripe.com', resp.data['session_url'])


class StripeIdentityWebhookTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user   = make_user(phone='+12125550200', role='agent')
        DocumentScan.objects.create(
            user                = self.user,
            document_type       = DocumentScan.DocType.STATE_ID,
            registration_number = 'vs_test_webhook_001',
            status              = DocumentScan.Status.UNREADABLE,
            extracted_fields    = {'provider': 'stripe_identity'},
        )

    @patch('stripe.Webhook.construct_event')
    @override_settings(**_STRIPE_SETTINGS)
    def test_verified_webhook_updates_document_scan(self, mock_construct):
        """identity.verification_session.verified → DocumentScan status = CLEAN."""
        event = {
            'type': 'identity.verification_session.verified',
            'data': {
                'object': {
                    'id':     'vs_test_webhook_001',
                    'status': 'verified',
                    'verified_outputs': {
                        'first_name': 'Jane',
                        'last_name':  'Doe',
                    },
                    'last_error': None,
                },
            },
        }
        mock_construct.return_value = event

        body = json.dumps(event, separators=(',', ':')).encode()
        resp = self.client.post(
            reverse('webhook-stripe-identity'),
            data=body,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fakesig',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['activated'])

        scan = DocumentScan.objects.get(registration_number='vs_test_webhook_001')
        self.assertEqual(scan.status, DocumentScan.Status.CLEAN)

    @patch('stripe.Webhook.construct_event')
    @override_settings(**_STRIPE_SETTINGS)
    def test_invalid_signature_returns_400(self, mock_construct):
        """Invalid Stripe Identity webhook signature → 400."""
        import stripe
        mock_construct.side_effect = stripe.error.SignatureVerificationError(
            'Invalid signature', 'fakesig'
        )
        body = b'{"type": "identity.verification_session.verified"}'
        resp = self.client.post(
            reverse('webhook-stripe-identity'),
            data=body,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=badsig',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_stripe_key_not_configured(self):
        """StripeIdentityProvider.is_configured() returns False when key absent."""
        with override_settings(STRIPE_SECRET_KEY=''):
            provider = StripeIdentityProvider()
            self.assertFalse(provider.is_configured())
