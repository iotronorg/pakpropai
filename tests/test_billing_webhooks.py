"""Tests for SafepayBillingWebhookView and BSecureBillingWebhookView."""

import hashlib
import hmac
import json

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

_SAFEPAY_SECRET  = 'test-safepay-secret'
_BSECURE_SECRET  = 'test-bsecure-secret'


def _safepay_post(client, payload, secret=_SAFEPAY_SECRET):
    body = json.dumps(payload, separators=(',', ':')).encode()
    sig  = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        reverse('safepay-billing-webhook'),
        data=body,
        content_type='application/json',
        HTTP_X_SFPY_SIGNATURE=sig,
    )


def _bsecure_post(client, payload, secret=_BSECURE_SECRET):
    body = json.dumps(payload, separators=(',', ':')).encode()
    sig  = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        reverse('bsecure-billing-webhook'),
        data=body,
        content_type='application/json',
        HTTP_X_BSECURE_SIGNATURE=sig,
    )


# ── Safepay billing webhook ────────────────────────────────────────────────────

class SafepayBillingWebhookTest(TestCase):

    def setUp(self):
        self.client = APIClient()

    @override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
    def test_missing_secret_returns_400(self):
        from unittest.mock import patch
        with patch('apps.config.services.SystemConfigService.get', return_value=''):
            resp = _safepay_post(self.client, {'event': 'payment.success'})
        self.assertEqual(resp.status_code, 400)

    def test_missing_signature_header_returns_400(self):
        from unittest.mock import patch
        with patch('apps.config.services.SystemConfigService.get', return_value=_SAFEPAY_SECRET):
            resp = self.client.post(
                reverse('safepay-billing-webhook'),
                data=b'{}',
                content_type='application/json',
                # no HTTP_X_SFPY_SIGNATURE header
            )
        self.assertEqual(resp.status_code, 400)

    def test_wrong_signature_returns_400(self):
        from unittest.mock import patch
        with patch('apps.config.services.SystemConfigService.get', return_value=_SAFEPAY_SECRET):
            resp = _safepay_post(self.client, {'event': 'payment.success'}, secret='wrong-secret')
        self.assertEqual(resp.status_code, 400)

    def test_valid_payment_success_activates_plan(self):
        from unittest.mock import patch
        payload = {
            'event': 'payment.success',
            'data': {'tracker': {'merchant_order_id': 'billing-org123-professional'}},
        }
        with patch('apps.config.services.SystemConfigService.get', return_value=_SAFEPAY_SECRET), \
             patch('apps.billing.gateway.activate_from_order') as mock_activate:
            resp = _safepay_post(self.client, payload)
        self.assertEqual(resp.status_code, 200)
        mock_activate.assert_called_once_with('billing-org123-professional')

    def test_duplicate_order_idempotent(self):
        from unittest.mock import patch
        from apps.billing.models import WebhookEvent
        WebhookEvent.objects.create(gateway='safepay', event_id='billing-org123-dup')

        payload = {
            'event': 'payment.success',
            'data': {'tracker': {'merchant_order_id': 'billing-org123-dup'}},
        }
        with patch('apps.config.services.SystemConfigService.get', return_value=_SAFEPAY_SECRET), \
             patch('apps.billing.gateway.activate_from_order') as mock_activate:
            resp = _safepay_post(self.client, payload)
        self.assertEqual(resp.status_code, 200)
        mock_activate.assert_not_called()


# ── bSecure billing webhook ────────────────────────────────────────────────────

class BSecureBillingWebhookTest(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_missing_secret_returns_400(self):
        from unittest.mock import patch
        with patch('apps.config.services.SystemConfigService.get', return_value=''):
            resp = _bsecure_post(self.client, {'status': 'completed'})
        self.assertEqual(resp.status_code, 400)

    def test_wrong_signature_returns_400(self):
        from unittest.mock import patch
        with patch('apps.config.services.SystemConfigService.get', return_value=_BSECURE_SECRET):
            resp = _bsecure_post(self.client, {'status': 'completed'}, secret='bad')
        self.assertEqual(resp.status_code, 400)

    def test_valid_completed_event_activates_plan(self):
        from unittest.mock import patch
        payload = {'status': 'COMPLETED', 'order_ref': 'billing-org456-basic'}
        with patch('apps.config.services.SystemConfigService.get', return_value=_BSECURE_SECRET), \
             patch('apps.billing.gateway.activate_from_order') as mock_activate:
            resp = _bsecure_post(self.client, payload)
        self.assertEqual(resp.status_code, 200)
        mock_activate.assert_called_once_with('billing-org456-basic')

    def test_duplicate_order_idempotent(self):
        from unittest.mock import patch
        from apps.billing.models import WebhookEvent
        WebhookEvent.objects.create(gateway='bsecure', event_id='billing-org456-dup')

        payload = {'status': 'completed', 'order_ref': 'billing-org456-dup'}
        with patch('apps.config.services.SystemConfigService.get', return_value=_BSECURE_SECRET), \
             patch('apps.billing.gateway.activate_from_order') as mock_activate:
            resp = _bsecure_post(self.client, payload)
        self.assertEqual(resp.status_code, 200)
        mock_activate.assert_not_called()
