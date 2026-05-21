"""
Tests for Phase 5: Safepay + bSecure deal-lock webhook routes.

Each gateway: 3 tests
  1. valid signature + paid  → deal activated, buyer + seller notified
  2. invalid signature       → 400
  3. idempotent re-delivery  → 200, activated=False, deal stays locked
"""
import hashlib
import hmac
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.escrow.models import EscrowDeal
from apps.properties.models import Property

User = get_user_model()

_SAFEPAY_SECRET  = 'test-safepay-secret'
_BSECURE_SECRET  = 'test-bsecure-secret'


def _user(phone='+923000000001', role='client'):
    return User.objects.create_user(phone=phone, password='pw', role=role)


def _property(title='Phase5 Property'):
    return Property.objects.create(
        listing_owner_type='platform',
        title=title,
        city='Lahore',
        location='DHA Phase 5',
        property_type='residential',
        price=5_000_000,
        area_marla=5,
    )


def _deal(buyer, prop):
    return EscrowDeal.objects.create(
        buyer=buyer,
        property=prop,
        token_amount=25_000,
        status=EscrowDeal.Status.INITIATED,
    )


def _signed_post(client, url_name, payload, secret, sig_header):
    body = json.dumps(payload, separators=(',', ':')).encode()
    sig  = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        reverse(url_name),
        data=body,
        content_type='application/json',
        **{sig_header: sig},
    )


# ── Safepay deal-lock webhook ─────────────────────────────────────────────────

@override_settings(SAFEPAY_SECRET_KEY=_SAFEPAY_SECRET)
class SafepayDealLockWebhookTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer  = _user(phone='+923001110001')
        self.prop   = _property()
        self.deal   = _deal(self.buyer, self.prop)

    def _payload(self, order_id=None, paid=True):
        return {
            'data': {
                'order_id': str(order_id or self.deal.id),
                'status':   'paid' if paid else 'failed',
                'tracker':  'TRK-sp-001',
                'amount':   25_000,
            }
        }

    @patch('apps.escrow.views._notify_seller_deal_locked')
    @patch('apps.escrow.views._notify_buyer_lock_active')
    def test_valid_signature_paid_activates_deal(self, mock_buyer, mock_seller):
        resp = _signed_post(
            self.client, 'webhook-safepay-deal-lock',
            self._payload(), _SAFEPAY_SECRET, 'HTTP_X_SAFEPAY_SIGNATURE',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)
        mock_buyer.assert_called_once_with(self.deal)
        mock_seller.assert_called_once_with(self.deal)

    def test_invalid_signature_returns_400(self):
        body = json.dumps(self._payload(), separators=(',', ':')).encode()
        resp = self.client.post(
            reverse('webhook-safepay-deal-lock'),
            data=body,
            content_type='application/json',
            HTTP_X_SAFEPAY_SIGNATURE='bad-sig',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.INITIATED)

    @patch('apps.escrow.views._notify_buyer_lock_active')
    def test_idempotent_already_locked_returns_200(self, _):
        self.deal.activate_lock()
        resp = _signed_post(
            self.client, 'webhook-safepay-deal-lock',
            self._payload(), _SAFEPAY_SECRET, 'HTTP_X_SAFEPAY_SIGNATURE',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)


# ── bSecure deal-lock webhook ─────────────────────────────────────────────────

@override_settings(BSECURE_CLIENT_SECRET=_BSECURE_SECRET)
class bSecureDealLockWebhookTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer  = _user(phone='+923002220001')
        self.prop   = _property(title='bSecure Property')
        self.deal   = _deal(self.buyer, self.prop)

    def _payload(self, order_id=None, paid=True):
        return {
            'status': 'paid' if paid else 'failed',
            'order':  {
                'order_ref': str(order_id or self.deal.id),
                'amount':    25_000,
            },
            'transaction_ref': 'TRK-bs-001',
        }

    @patch('apps.escrow.views._notify_seller_deal_locked')
    @patch('apps.escrow.views._notify_buyer_lock_active')
    def test_valid_signature_paid_activates_deal(self, mock_buyer, mock_seller):
        resp = _signed_post(
            self.client, 'webhook-bsecure-deal-lock',
            self._payload(), _BSECURE_SECRET, 'HTTP_X_BSECURE_SIGNATURE',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)
        mock_buyer.assert_called_once_with(self.deal)
        mock_seller.assert_called_once_with(self.deal)

    def test_invalid_signature_returns_400(self):
        body = json.dumps(self._payload(), separators=(',', ':')).encode()
        resp = self.client.post(
            reverse('webhook-bsecure-deal-lock'),
            data=body,
            content_type='application/json',
            HTTP_X_BSECURE_SIGNATURE='bad-sig',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.INITIATED)

    @patch('apps.escrow.views._notify_buyer_lock_active')
    def test_idempotent_already_locked_returns_200(self, _):
        self.deal.activate_lock()
        resp = _signed_post(
            self.client, 'webhook-bsecure-deal-lock',
            self._payload(), _BSECURE_SECRET, 'HTTP_X_BSECURE_SIGNATURE',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)
