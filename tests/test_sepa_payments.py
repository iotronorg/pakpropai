"""
Tests for GLOBAL-6: SEPA Direct Debit for EU deal payments.

5 tests:
  1. EU org deal lock response includes 'sepa_debit' in available_payment_methods
  2. Non-EU org deal lock response does NOT include 'sepa_debit'
  3. SEPA PaymentIntent created with correct EUR currency
  4. mandate_accepted missing/false → 400
  5. Stripe payment_intent.succeeded webhook → EU deal transitions to LOCKED
"""
import json
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.escrow.models import EscrowDeal
from apps.markets.registry import EU_COUNTRIES, is_eu_country
from tests.factories import make_user, make_org, make_property, make_deal

_STRIPE_SETTINGS = {
    'STRIPE_SECRET_KEY':                'sk_test_fake',
    'STRIPE_DEAL_LOCK_WEBHOOK_SECRET':  'whsec_test_fake',
}


class EuCountryHelperTests(TestCase):

    def test_eu_countries_constant_populated(self):
        """EU_COUNTRIES contains expected members."""
        for c in ('DE', 'FR', 'NL', 'PL', 'ES'):
            self.assertIn(c, EU_COUNTRIES)

    def test_is_eu_country_true_for_eu(self):
        self.assertTrue(is_eu_country('DE'))
        self.assertTrue(is_eu_country('fr'))   # case-insensitive

    def test_is_eu_country_false_for_non_eu(self):
        self.assertFalse(is_eu_country('PK'))
        self.assertFalse(is_eu_country('AE'))
        self.assertFalse(is_eu_country('US'))


class AvailablePaymentMethodsTests(TestCase):
    """Tests 1 & 2 — SEPA offered to EU orgs, not to others."""

    def setUp(self):
        self.client  = APIClient()
        self.buyer   = make_user(phone='+491234567890', role='client')
        self.client.force_authenticate(user=self.buyer)

    def test_eu_org_deal_lock_includes_sepa(self):
        """EU org deal lock response includes sepa_debit in available_payment_methods."""
        eu_org  = make_org(name='Berlin Immobilien', country='DE')
        eu_prop = make_property(org=eu_org, title='Berlin Flat')

        resp = self.client.post(reverse('deal-initiate'), {
            'property_id':     str(eu_prop.pk),
            'token_amount':    25000,
            'payment_gateway': 'stripe',
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIn('sepa_debit', resp.data.get('available_payment_methods', []))

    def test_non_eu_org_deal_lock_excludes_sepa(self):
        """Non-EU org (AE) does NOT include sepa_debit in available_payment_methods."""
        ae_org  = make_org(name='Dubai Realty', country='AE')
        ae_prop = make_property(org=ae_org, title='Dubai Apt')

        resp = self.client.post(reverse('deal-initiate'), {
            'property_id':     str(ae_prop.pk),
            'token_amount':    25000,
            'payment_gateway': 'stripe',
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertNotIn('sepa_debit', resp.data.get('available_payment_methods', []))


class SepaPaymentIntentTests(TestCase):
    """Tests 3 & 4 — PaymentIntent creation and mandate validation."""

    def setUp(self):
        self.client  = APIClient()
        self.eu_org  = make_org(name='Paris Immo', country='FR')
        self.buyer   = make_user(phone='+33612345678', role='client')
        self.eu_prop = make_property(org=self.eu_org, title='Paris Flat')
        self.deal    = make_deal(
            buyer        = self.buyer,
            prop         = self.eu_prop,
            token_amount = 25000,
            currency     = 'EUR',
            payment_gateway = 'stripe',
        )
        self.client.force_authenticate(user=self.buyer)

    @patch('stripe.PaymentMethod.create')
    @patch('stripe.PaymentIntent.create')
    @override_settings(**_STRIPE_SETTINGS)
    def test_sepa_payment_intent_uses_eur_currency(self, mock_pi, mock_pm):
        """SEPA PaymentIntent is created with EUR currency."""
        mock_pm.return_value = MagicMock(id='pm_test_sepa')
        mock_pi_obj = MagicMock()
        mock_pi_obj.id             = 'pi_test_sepa_001'
        mock_pi_obj.client_secret  = 'pi_test_sepa_001_secret_xyz'
        mock_pi_obj.status         = 'processing'
        mock_pi.return_value       = mock_pi_obj

        resp = self.client.post(
            reverse('payment-sepa', kwargs={'deal_id': str(self.deal.id)}),
            {
                'iban':             'DE89370400440532013000',
                'account_name':     'Hans Müller',
                'mandate_accepted': True,
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data['payment_intent_id'], 'pi_test_sepa_001')

        # Verify Stripe was called with EUR
        _, pi_kwargs = mock_pi.call_args
        self.assertEqual(pi_kwargs.get('currency') or mock_pi.call_args[1].get('currency'), 'eur')

    def test_missing_mandate_returns_400(self):
        """SEPA endpoint returns 400 when mandate_accepted is not true."""
        resp = self.client.post(
            reverse('payment-sepa', kwargs={'deal_id': str(self.deal.id)}),
            data=json.dumps({
                'iban':             'DE89370400440532013000',
                'account_name':     'Hans Müller',
                'mandate_accepted': False,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('mandate', resp.data['detail'].lower())


class SepaWebhookTest(TestCase):
    """Test 5 — payment_intent.succeeded webhook activates EU deal lock."""

    def setUp(self):
        self.client  = APIClient()
        self.eu_org  = make_org(name='Amsterdam Vastgoed', country='NL')
        self.buyer   = make_user(phone='+31612345678', role='client')
        eu_prop      = make_property(org=self.eu_org, title='Amsterdam Canal House')
        self.deal    = make_deal(
            buyer           = self.buyer,
            prop            = eu_prop,
            token_amount    = 25000,
            currency        = 'EUR',
            payment_gateway = 'stripe',
        )

    @patch('stripe.Webhook.construct_event')
    @override_settings(**_STRIPE_SETTINGS)
    def test_payment_intent_succeeded_locks_deal(self, mock_construct):
        """payment_intent.succeeded for SEPA deal → deal transitions to LOCKED."""
        event = {
            'type': 'payment_intent.succeeded',
            'data': {
                'object': {
                    'id':       'pi_sepa_nl_001',
                    'amount':   2500000,
                    'metadata': {'order_id': str(self.deal.id)},
                },
            },
        }
        mock_construct.return_value = event

        body = json.dumps(event, separators=(',', ':')).encode()
        resp = self.client.post(
            reverse('webhook-stripe-deal-lock'),
            data=body,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fakesig',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['activated'])

        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)
