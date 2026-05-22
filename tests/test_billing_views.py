"""Tests for billing views: portal, invoices, payment settings."""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from tests.factories import make_developer, make_user


def _developer(phone='+923001111001'):
    return make_developer(phone=phone)


def _agent(phone='+923001111002'):
    return make_user(phone=phone, role='agent')


class OrgPaymentSettingsViewTest(TestCase):
    """GET + PATCH /billing/payment-settings/ — developer only."""

    def setUp(self):
        self.client = APIClient()
        self.dev, self.org = _developer()

    def test_get_returns_payment_settings_for_developer(self):
        self.client.force_authenticate(user=self.dev)
        resp = self.client.get(reverse('billing-payment-settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('gateway', resp.data)

    def test_get_forbidden_for_agent(self):
        agent = _agent()
        self.client.force_authenticate(user=agent)
        resp = self.client.get(reverse('billing-payment-settings'))
        self.assertEqual(resp.status_code, 403)

    def test_patch_updates_gateway(self):
        self.client.force_authenticate(user=self.dev)
        resp = self.client.patch(
            reverse('billing-payment-settings'),
            {'gateway': 'safepay', 'safepay_merchant_key': 'mk_test'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get('saved'))

    def test_patch_does_not_clear_sensitive_fields_when_blank_sent(self):
        from apps.organizations.models import OrgPaymentSettings
        ps, _ = OrgPaymentSettings.objects.get_or_create(organization=self.org)
        ps.safepay_secret_key = 'existing-secret'
        ps.save(update_fields=['safepay_secret_key'])

        self.client.force_authenticate(user=self.dev)
        self.client.patch(
            reverse('billing-payment-settings'),
            {'safepay_secret_key': ''},   # blank = keep existing
            format='json',
        )

        ps.refresh_from_db()
        self.assertEqual(ps.safepay_secret_key, 'existing-secret')

    def test_get_masks_sensitive_fields(self):
        from apps.organizations.models import OrgPaymentSettings
        ps, _ = OrgPaymentSettings.objects.get_or_create(organization=self.org)
        ps.safepay_secret_key = 'real-secret'
        ps.save(update_fields=['safepay_secret_key'])

        self.client.force_authenticate(user=self.dev)
        resp = self.client.get(reverse('billing-payment-settings'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get('safepay_secret_key'), '__configured__')


class BillingPortalViewTest(TestCase):
    """POST /billing/portal/ — requires Stripe customer ID."""

    def setUp(self):
        self.client = APIClient()
        self.dev, self.org = _developer(phone='+923001111010')

    def test_returns_400_when_stripe_not_configured(self):
        self.client.force_authenticate(user=self.dev)
        with patch('apps.billing.gateway._cfg', return_value=''):
            resp = self.client.post(reverse('billing-portal'))
        # 400 because no Stripe secret key configured
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_returns_401(self):
        resp = self.client.post(reverse('billing-portal'))
        self.assertEqual(resp.status_code, 401)


class BillingInvoiceViewTest(TestCase):
    """GET /billing/invoices/ — returns empty list gracefully when no subscription."""

    def setUp(self):
        self.client = APIClient()
        self.dev, self.org = _developer(phone='+923001111020')

    def test_returns_empty_invoices_when_no_subscription(self):
        self.client.force_authenticate(user=self.dev)
        with patch('apps.billing.gateway._cfg', return_value=''):
            resp = self.client.get(reverse('billing-invoices'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get('invoices'), [])

    def test_unauthenticated_returns_401(self):
        resp = self.client.get(reverse('billing-invoices'))
        self.assertEqual(resp.status_code, 401)
