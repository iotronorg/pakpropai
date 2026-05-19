import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.agents.models import Agent
from apps.escrow.models import EscrowDeal
from apps.properties.models import Property

from django.contrib.auth import get_user_model
User = get_user_model()

_SAFEPAY_TEST_SECRET = 'test-safepay-secret'


def _user(phone='+923001234567', role='client'):
    return User.objects.create_user(phone=phone, password='pw', role=role)


def _agent_user(phone='+923009876543'):
    u = _user(phone=phone, role='agent')
    a = Agent.objects.create(user=u, name='Test Agent')
    return u, a


def _property(owner, agent=None):
    return Property.objects.create(
        owner=owner,
        title='Test Property',
        city='Lahore',
        location='DHA Phase 5',
        property_type='residential',
        price=5_000_000,
        area_marla=5,
        assigned_agent=agent,
    )


def _deal(buyer, prop, agent=None):
    return EscrowDeal.objects.create(
        buyer=buyer,
        property=prop,
        agent=agent,
        token_amount=25_000,
        status=EscrowDeal.Status.INITIATED,
    )


def _safepay_webhook_post(client, payload, secret=_SAFEPAY_TEST_SECRET):
    """Post a webhook with a correctly-signed HMAC body."""
    body = json.dumps(payload, separators=(',', ':')).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        reverse('webhook-safepay'),
        data=body,
        content_type='application/json',
        HTTP_X_SAFEPAY_SIGNATURE=sig,
    )


class PaymentCheckoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = _user()
        _, self.agent = _agent_user()
        owner = _user(phone='+923000000001', role='agent')
        self.prop = _property(owner=owner, agent=self.agent)
        self.deal = _deal(buyer=self.buyer, prop=self.prop, agent=self.agent)

    @patch('apps.config.services.SystemConfigService.get_active_gateway', return_value='safepay')
    @patch('apps.payments.services.SafepayGateway.create_checkout')
    def test_checkout_creates_payment_and_returns_url(self, mock_sp, _mock_gw):
        mock_sp.return_value = {
            'checkout_token': 'tok_abc',
            'checkout_url': 'https://sandbox.getsafepay.com/checkout?token=tok_abc',
        }
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('payment-checkout', kwargs={'deal_id': self.deal.pk}),
            data={'gateway': 'safepay'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('checkout_url', resp.data)
        from apps.payments.models import Payment
        self.assertTrue(Payment.objects.filter(escrow_deal=self.deal).exists())

    def test_checkout_requires_authentication(self):
        resp = self.client.post(
            reverse('payment-checkout', kwargs={'deal_id': self.deal.pk}),
            data={'gateway': 'safepay'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('apps.config.services.SystemConfigService.get_active_gateway', return_value='safepay')
    def test_checkout_blocked_when_deal_not_initiated(self, _):
        self.deal.status = EscrowDeal.Status.LOCKED
        self.deal.save(update_fields=['status'])
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('payment-checkout', kwargs={'deal_id': self.deal.pk}),
            data={'gateway': 'safepay'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_checkout_blocked_for_non_buyer(self):
        other = _user(phone='+923005555555')
        self.client.force_authenticate(user=other)
        resp = self.client.post(
            reverse('payment-checkout', kwargs={'deal_id': self.deal.pk}),
            data={'gateway': 'safepay'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_checkout_blocked_when_gateway_is_manual(self):
        self.client.force_authenticate(user=self.buyer)
        with patch('apps.config.services.SystemConfigService.get_active_gateway', return_value='manual'):
            resp = self.client.post(
                reverse('payment-checkout', kwargs={'deal_id': self.deal.pk}),
                data={'gateway': 'safepay'},
                format='json',
            )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(SAFEPAY_SECRET_KEY=_SAFEPAY_TEST_SECRET)
class SafepayWebhookTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = _user()
        _, self.agent = _agent_user()
        owner = _user(phone='+923000000002', role='agent')
        self.prop = _property(owner=owner, agent=self.agent)
        self.deal = _deal(buyer=self.buyer, prop=self.prop, agent=self.agent)

    def _success_payload(self, order_id=None):
        return {
            'event': 'payment:success',
            'data': {
                'order_id': str(order_id or self.deal.id),
                'tracker': 'TRK-abc123',
                'status': 'paid',
            },
        }

    @patch('apps.escrow.views._notify_buyer_lock_active')
    def test_valid_webhook_activates_deal(self, mock_notify):
        resp = _safepay_webhook_post(self.client, self._success_payload())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)
        mock_notify.assert_called_once()

    def test_invalid_hmac_returns_ok_but_not_activated(self):
        payload = self._success_payload()
        body = json.dumps(payload, separators=(',', ':')).encode()
        resp = self.client.post(
            reverse('webhook-safepay'),
            data=body,
            content_type='application/json',
            HTTP_X_SAFEPAY_SIGNATURE='deadbeef',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertNotEqual(self.deal.status, EscrowDeal.Status.LOCKED)

    @patch('apps.escrow.views._notify_buyer_lock_active')
    def test_duplicate_webhook_is_idempotent(self, _mock_notify):
        self.deal.status = EscrowDeal.Status.LOCKED
        self.deal.activate_lock()

        resp = _safepay_webhook_post(self.client, self._success_payload())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)

    def test_failed_payment_does_not_activate_deal(self):
        payload = {
            'event': 'payment:failed',
            'data': {
                'order_id': str(self.deal.id),
                'tracker': 'TRK-abc123',
                'status': 'failed',
            },
        }
        resp = _safepay_webhook_post(self.client, payload)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertNotEqual(self.deal.status, EscrowDeal.Status.LOCKED)

    def test_unknown_order_id_returns_ok_but_not_activated(self):
        import uuid
        payload = self._success_payload(order_id=uuid.uuid4())
        resp = _safepay_webhook_post(self.client, payload)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])


class PaymentReturnViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = _user()
        _, self.agent = _agent_user()
        owner = _user(phone='+923000000003', role='agent')
        self.prop = _property(owner=owner, agent=self.agent)
        self.deal = _deal(buyer=self.buyer, prop=self.prop, agent=self.agent)

    def test_success_return_returns_200(self):
        resp = self.client.get(
            reverse('payment-return'),
            {'deal_id': str(self.deal.pk), 'status': 'success'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('deal_id', resp.data)

    def test_cancelled_return_does_not_activate_deal(self):
        resp = self.client.get(
            reverse('payment-return'),
            {'deal_id': str(self.deal.pk), 'status': 'cancelled'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.deal.refresh_from_db()
        self.assertNotEqual(self.deal.status, EscrowDeal.Status.LOCKED)

    def test_missing_deal_id_returns_400(self):
        resp = self.client.get(reverse('payment-return'), {'status': 'success'})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nonexistent_deal_returns_404(self):
        import uuid
        resp = self.client.get(
            reverse('payment-return'),
            {'deal_id': str(uuid.uuid4()), 'status': 'success'},
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
