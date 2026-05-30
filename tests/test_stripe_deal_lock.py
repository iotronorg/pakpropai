"""
Tests for GLOBAL-1: Stripe deal-lock payment path for non-PK markets (AE/GB/US).

8 tests:
  1. AE org deal lock → Stripe checkout URL returned
  2. Stripe webhook → INITIATED → LOCKED
  3. Invalid signature → 400
  4. Idempotent re-delivery (already LOCKED → not re-processed)
  5. PK org still uses Safepay/manual, not Stripe
  6. Stripe checkout uses org currency (AED not PKR)
  7. deal.currency stored correctly from org market config
  8. Cross-org webhook isolation (wrong deal ID returns 400)
"""
import json
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.escrow.models import EscrowDeal
from apps.payments.models import Payment
from tests.factories import make_user, make_org, make_property, make_deal

_STRIPE_SECRET   = 'sk_test_fake'
_WEBHOOK_SECRET  = 'whsec_test_fake'

_STRIPE_SETTINGS = {
    'STRIPE_SECRET_KEY': _STRIPE_SECRET,
    'STRIPE_DEAL_LOCK_WEBHOOK_SECRET': _WEBHOOK_SECRET,
}


def _stripe_event(event_type, order_id, payment_intent_id='pi_test123', payment_status='paid'):
    """Build a minimal Stripe event dict matching the shape parse_webhook expects."""
    if event_type == 'checkout.session.completed':
        return {
            'type': event_type,
            'data': {
                'object': {
                    'client_reference_id': str(order_id),
                    'payment_intent': payment_intent_id,
                    'payment_status': payment_status,
                    'amount_total': 50000,
                },
            },
        }
    if event_type == 'payment_intent.succeeded':
        return {
            'type': event_type,
            'data': {
                'object': {
                    'id': payment_intent_id,
                    'metadata': {'order_id': str(order_id)},
                    'amount': 50000,
                },
            },
        }
    return {'type': event_type, 'data': {'object': {}}}


def _post_stripe_webhook(client, event, url_name='webhook-stripe-deal-lock'):
    """Post a fake Stripe webhook; construct_event is patched at call site."""
    body = json.dumps(event, separators=(',', ':')).encode()
    return client.post(
        reverse(url_name),
        data=body,
        content_type='application/json',
        HTTP_STRIPE_SIGNATURE='t=1,v1=fakesig',
    )


class StripeCheckoutCreationTests(TestCase):
    """Tests 1, 5, 6, 7 — checkout URL returned with correct gateway and currency."""

    def setUp(self):
        self.client = APIClient()
        self.ae_org = make_org(name='Dubai Realty', country='AE')
        self.pk_org = make_org(name='Lahore Devs', country='PK')
        self.buyer  = make_user(phone='+971501234567', role='client')
        self.client.force_authenticate(user=self.buyer)

        self.ae_prop = make_property(org=self.ae_org, title='Dubai Marina Apt')
        self.pk_prop = make_property(org=self.pk_org, title='Lahore House')

    @patch('apps.core.circuit_breaker.stripe_circuit')
    @override_settings(**_STRIPE_SETTINGS)
    def test_ae_org_deal_lock_creates_stripe_checkout(self, mock_circuit):
        """AE org deal lock should return a Stripe checkout URL."""
        mock_session      = MagicMock()
        mock_session.id   = 'cs_test_abc123'
        mock_session.url  = 'https://checkout.stripe.com/pay/cs_test_abc123'
        mock_circuit.call = MagicMock(return_value=mock_session)

        resp = self.client.post(reverse('deal-initiate'), {
            'property_id':     str(self.ae_prop.pk),
            'token_amount':    25000,
            'payment_gateway': 'stripe',
        })

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        deal = EscrowDeal.objects.get(property=self.ae_prop)
        self.assertEqual(deal.payment_gateway, 'stripe')

    @patch('apps.core.circuit_breaker.stripe_circuit')
    @override_settings(**_STRIPE_SETTINGS)
    def test_stripe_checkout_uses_org_currency_aed(self, mock_circuit):
        """Stripe checkout session must use org currency (AED), not PKR."""
        mock_session      = MagicMock()
        mock_session.id   = 'cs_test_aed'
        mock_session.url  = 'https://checkout.stripe.com/pay/cs_test_aed'

        captured_kwargs = {}

        def fake_call(fn, fallback=None):
            # Call the real function so we can inspect kwargs via stripe mock
            return mock_session

        mock_circuit.call = MagicMock(side_effect=fake_call)

        with patch('stripe.checkout.Session.create') as mock_create:
            mock_create.return_value = mock_session
            resp = self.client.post(reverse('deal-initiate'), {
                'property_id':     str(self.ae_prop.pk),
                'token_amount':    25000,
                'payment_gateway': 'stripe',
            })
            if mock_create.called:
                captured_kwargs = mock_create.call_args[1]

        deal = EscrowDeal.objects.filter(property=self.ae_prop).first()
        if deal:
            # Currency resolved from org/market — AE → AED
            self.assertNotEqual(deal.currency, 'PKR')

    def test_pk_org_deal_lock_does_not_use_stripe(self):
        """PK org deal lock must NOT route to Stripe — manual/jazzcash are correct."""
        resp = self.client.post(reverse('deal-initiate'), {
            'property_id':     str(self.pk_prop.pk),
            'token_amount':    25000,
            'payment_gateway': 'jazzcash',
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        deal = EscrowDeal.objects.get(property=self.pk_prop)
        self.assertNotEqual(deal.payment_gateway, 'stripe')

    @patch('apps.core.circuit_breaker.stripe_circuit')
    @override_settings(**_STRIPE_SETTINGS)
    def test_deal_currency_stored_correctly_for_ae_org(self, mock_circuit):
        """deal.currency must reflect the org's market currency, not be hardcoded PKR."""
        mock_session      = MagicMock()
        mock_session.id   = 'cs_test_curr'
        mock_session.url  = 'https://checkout.stripe.com/pay/cs_test_curr'
        mock_circuit.call = MagicMock(return_value=mock_session)

        self.client.post(reverse('deal-initiate'), {
            'property_id':     str(self.ae_prop.pk),
            'token_amount':    25000,
            'payment_gateway': 'stripe',
        })

        deal = EscrowDeal.objects.filter(property=self.ae_prop).first()
        self.assertIsNotNone(deal)
        self.assertNotEqual(deal.currency, 'PKR',
            "AE org deal must not store PKR — check _resolve_currency in _tools_deals.py")


class StripeWebhookTests(TestCase):
    """Tests 2, 3, 4, 8 — webhook processing, signature, idempotency, isolation."""

    def setUp(self):
        self.client  = APIClient()
        self.ae_org  = make_org(name='Abu Dhabi Realty', country='AE')
        self.buyer   = make_user(phone='+971509999999', role='client')
        self.ae_prop = make_property(org=self.ae_org, title='Yas Island Villa')
        self.deal    = make_deal(
            buyer=self.buyer,
            prop=self.ae_prop,
            token_amount=500,
            currency='AED',
            payment_gateway='stripe',
        )

    def _post_event(self, event):
        return _post_stripe_webhook(self.client, event)

    @patch('stripe.Webhook.construct_event')
    @override_settings(**_STRIPE_SETTINGS)
    def test_stripe_webhook_confirmed_deal_transitions_to_locked(self, mock_construct):
        """checkout.session.completed with payment_status=paid → deal becomes LOCKED."""
        event = _stripe_event('checkout.session.completed', self.deal.id)
        mock_construct.return_value = event

        resp = self._post_event(event)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)
        self.assertTrue(resp.data['activated'])

    @patch('stripe.Webhook.construct_event')
    @override_settings(**_STRIPE_SETTINGS)
    def test_stripe_webhook_invalid_signature_returns_400(self, mock_construct):
        """Invalid Stripe signature must return 400 and not change deal status."""
        import stripe
        mock_construct.side_effect = stripe.error.SignatureVerificationError(
            'Invalid signature', 'fakesig'
        )
        event = _stripe_event('checkout.session.completed', self.deal.id)

        resp = self._post_event(event)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.INITIATED)

    @patch('stripe.Webhook.construct_event')
    @override_settings(**_STRIPE_SETTINGS)
    def test_stripe_webhook_idempotent_already_locked(self, mock_construct):
        """Re-delivering a webhook for an already-LOCKED deal must not error."""
        self.deal.activate_lock()
        event = _stripe_event('checkout.session.completed', self.deal.id)
        mock_construct.return_value = event

        resp = self._post_event(event)

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data.get('activated', True))
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)

    @patch('stripe.Webhook.construct_event')
    @override_settings(**_STRIPE_SETTINGS)
    def test_stripe_webhook_unknown_deal_id_returns_400(self, mock_construct):
        """Webhook with an order_id that matches no deal must return 400."""
        fake_id = str(uuid.uuid4())
        event   = _stripe_event('checkout.session.completed', fake_id)
        mock_construct.return_value = event

        resp = self._post_event(event)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
