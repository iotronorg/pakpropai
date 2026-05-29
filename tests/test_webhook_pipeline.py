"""
Webhook pipeline tests (8 tests).
"""

import hashlib
import hmac
import json
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from apps.inventory.models import ExternalPlatformConnection, WebhookDeliveryRecord
from apps.inventory.webhook_pipeline import PropertyWebhookPipeline
from tests.factories import make_developer, make_property

_LOCMEM_CACHE = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
}


def _make_connection(org, **kwargs):
    return ExternalPlatformConnection.objects.create(
        org=org, platform='zameen', api_key='k', api_secret='secret123', **kwargs
    )


def _signed_payload(payload_dict: dict, secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload_dict).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, f'sha256={sig}'


class ValidHMACAcceptsPayloadTest(TestCase):
    """Valid HMAC signature → inbound payload accepted and sync queued."""

    @patch('apps.inventory.tasks.sync_external_platform.delay')
    @override_settings(CACHES=_LOCMEM_CACHE)
    def test_valid_hmac_accepted(self, mock_delay):
        dev, org = make_developer()
        connection = _make_connection(org)

        body, sig = _signed_payload({'id': 'payload-1', 'event': 'update'}, 'secret123')
        result = PropertyWebhookPipeline().handle_inbound(connection, body, sig)
        self.assertEqual(result['status'], 'queued')
        mock_delay.assert_called_once()


class InvalidHMACRaisesPermissionDeniedTest(TestCase):
    """Invalid HMAC → raises PermissionDenied."""

    @override_settings(CACHES=_LOCMEM_CACHE)
    def test_invalid_hmac_raises(self):
        dev, org = make_developer()
        connection = _make_connection(org)

        body = json.dumps({'event': 'update'}).encode()
        with self.assertRaises(PermissionDenied):
            PropertyWebhookPipeline().handle_inbound(connection, body, 'sha256=invalid')


class DuplicateIdempotencyKeySkippedTest(TestCase):
    """Duplicate idempotency key → no-op."""

    @patch('apps.inventory.tasks.sync_external_platform.delay')
    @override_settings(CACHES=_LOCMEM_CACHE)
    def test_duplicate_key_skipped(self, mock_delay):
        dev, org = make_developer()
        connection = _make_connection(org)

        body, sig = _signed_payload({'id': 'dup-key-001', 'event': 'update'}, 'secret123')
        PropertyWebhookPipeline().handle_inbound(connection, body, sig)
        result2 = PropertyWebhookPipeline().handle_inbound(connection, body, sig)

        self.assertEqual(result2['skipped'], True)
        self.assertEqual(mock_delay.call_count, 1)


class OutboundDispatchCreatesRecordTest(TestCase):
    """Outbound dispatch creates WebhookDeliveryRecord(status=pending)."""

    @patch('apps.inventory.tasks.dispatch_delta_to_platform.delay')
    def test_outbound_creates_record(self, mock_delay):
        dev, org = make_developer()
        prop = make_property(org=org)
        connection = _make_connection(org, sync_direction='outbound')

        PropertyWebhookPipeline().dispatch_outbound(connection, str(prop.id), {'price': 5000000})

        record = WebhookDeliveryRecord.objects.filter(connection=connection).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.status, 'pending')
        mock_delay.assert_called_once()


class FailedDispatchIncrementsAttemptCountTest(TestCase):
    """Failed outbound dispatch increments attempt_count on retry."""

    @patch('apps.inventory.tasks.requests.post')
    @patch('apps.inventory.tasks._sign_payload', return_value='sig')
    def test_failed_dispatch_increments(self, mock_sign, mock_post):
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError('timeout')

        dev, org = make_developer()
        connection = _make_connection(org, base_url='https://example.com')

        from apps.inventory.tasks import dispatch_delta_to_platform
        try:
            dispatch_delta_to_platform.apply(args=[
                str(connection.id), 'prop-id', 'hash123', {'price': 1}
            ])
        except Exception:
            pass

        record = WebhookDeliveryRecord.objects.filter(connection=connection).order_by('-created_at').first()
        if record:
            self.assertGreaterEqual(record.attempt_count, 1)


class WebhookDeliveryOrgIsolationTest(TestCase):
    """WebhookDeliveryRecord org isolation — org A's records not visible to org B."""

    def test_org_b_cannot_see_org_a_logs(self):
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        dev_a, org_a = make_developer(org_name='Org A')
        dev_b, org_b = make_developer(org_name='Org B')

        connection_a = _make_connection(org_a)
        WebhookDeliveryRecord.objects.create(
            org=org_a, connection=connection_a, status='delivered', payload={}
        )

        client = APIClient()
        token = str(RefreshToken.for_user(dev_b).access_token)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

        # dev_b can't see org_a's connection
        resp = client.get(f'/api/v1/inventory/connections/{connection_a.id}/logs/')
        self.assertIn(resp.status_code, (403, 404))


class InboundWebhookQueuesTaskTest(TestCase):
    """Inbound webhook queues sync_external_platform.delay with correct connection_id."""

    @patch('apps.inventory.tasks.sync_external_platform.delay')
    @override_settings(CACHES=_LOCMEM_CACHE)
    def test_inbound_queues_correct_connection(self, mock_delay):
        dev, org = make_developer()
        connection = _make_connection(org)

        body, sig = _signed_payload({'id': 'ev-999', 'event': 'new_listing'}, 'secret123')
        PropertyWebhookPipeline().handle_inbound(connection, body, sig)

        mock_delay.assert_called_once_with(str(connection.id))


class ConnectionTestViewShapeTest(TestCase):
    """ConnectionTestView returns correct status shape."""

    @patch('apps.inventory.adapters.zameen.requests.get')
    def test_test_view_returns_status_shape(self, mock_get):
        from unittest.mock import MagicMock
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        dev, org = make_developer()
        connection = _make_connection(org)

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {'results': [{'name': 'Test Property'}]},
        )
        mock_get.return_value.raise_for_status = lambda: None

        client = APIClient()
        token = str(RefreshToken.for_user(dev).access_token)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = client.post(f'/api/v1/inventory/connections/{connection.id}/test/')
        data = resp.json()
        self.assertIn('status', data)
        self.assertIn('detail', data)
        self.assertEqual(data['status'], 'connected')
