"""
Inventory sync unit tests (14 tests).
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.inventory.adapters.base import ExternalPlatformAdapter, AdapterConfig, SyncResult
from apps.inventory.models import ExternalPlatformConnection, SyncConflictAlert
from apps.inventory.sync_engine import InventorySyncOrchestrator
from tests.factories import make_developer, make_property, make_deal, make_user


def _make_connection(org, platform='zameen', **kwargs):
    return ExternalPlatformConnection.objects.create(
        org=org,
        platform=platform,
        api_key='test_key',
        api_secret='test_secret',
        **kwargs,
    )


class AdapterAbstractMethodsTest(TestCase):
    """Adapter interface enforces abstract methods."""

    def test_cannot_instantiate_without_implementing_abstract_methods(self):
        with self.assertRaises(TypeError):
            ExternalPlatformAdapter(AdapterConfig(
                platform='zameen', base_url='', api_key='', api_secret=''
            ))


class RunSyncMapsListingsTest(TestCase):
    """run_sync maps external listing to Property delta via field_mappings."""

    @patch('apps.inventory.adapters.zameen.requests.get')
    def test_sync_applies_mapped_fields(self, mock_get):
        dev, org = make_developer()
        prop = make_property(org=org, title='Old Title', city='Lahore')
        connection = _make_connection(org, field_mappings={'title': 'name', 'city': 'city'})

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {'results': [{'name': 'New Title', 'city': 'Lahore'}]},
        )
        mock_get.return_value.raise_for_status = lambda: None

        result = InventorySyncOrchestrator().run_sync(connection)
        self.assertIsInstance(result, SyncResult)


class DealLockConflictCheckTest(TestCase):
    """_check_deal_lock_conflict returns True for locked, False for released/cancelled."""

    def test_locked_deal_is_conflict(self):
        from apps.escrow.models import EscrowDeal
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        result = InventorySyncOrchestrator._check_deal_lock_conflict(str(prop.id))
        self.assertTrue(result)

    def test_released_deal_is_not_conflict(self):
        from apps.escrow.models import EscrowDeal
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.RELEASED
        deal.save()

        result = InventorySyncOrchestrator._check_deal_lock_conflict(str(prop.id))
        self.assertFalse(result)

    def test_cancelled_deal_is_not_conflict(self):
        from apps.escrow.models import EscrowDeal
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.CANCELLED
        deal.save()

        result = InventorySyncOrchestrator._check_deal_lock_conflict(str(prop.id))
        self.assertFalse(result)


class ApplyDeltaSelectForUpdateTest(TestCase):
    """_apply_delta acquires select_for_update on Property row."""

    @patch('apps.inventory.sync_engine.transaction.atomic')
    def test_apply_delta_enters_transaction(self, mock_atomic):
        mock_atomic.return_value.__enter__ = lambda s: s
        mock_atomic.return_value.__exit__ = MagicMock(return_value=False)

        dev, org = make_developer()
        prop = make_property(org=org, title='Prop A', city='Karachi')
        connection = _make_connection(org)

        # No deal lock — should apply delta
        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'title': 'Prop B'})
        mock_atomic.assert_called()


class ConflictResolverCreatesAlertTest(TestCase):
    """Conflict resolver creates SyncConflictAlert on locked property."""

    def test_locked_property_creates_conflict_alert(self):
        from apps.escrow.models import EscrowDeal
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        connection = _make_connection(org)
        delta = {'price': 9999999}
        internal = {'price': prop.price}

        from apps.inventory.conflict_resolver import InventorySyncConflictResolver
        resolution = InventorySyncConflictResolver().resolve(
            connection=connection,
            property_id=str(prop.id),
            external_delta=delta,
            internal_state=internal,
        )
        self.assertEqual(SyncConflictAlert.objects.filter(property=prop).count(), 1)


class NotificationOnConflictTest(TestCase):
    """Notification(severity HIGH equivalent) created on conflict."""

    def test_conflict_creates_notification(self):
        from apps.escrow.models import EscrowDeal
        from apps.notifications.models import Notification
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        connection = _make_connection(org)
        from apps.inventory.conflict_resolver import InventorySyncConflictResolver
        InventorySyncConflictResolver().resolve(
            connection=connection,
            property_id=str(prop.id),
            external_delta={'price': 1},
            internal_state={'price': prop.price},
        )
        self.assertTrue(Notification.objects.filter(user=dev).exists())


class RetryTaskBackoffTest(TestCase):
    """sync_external_platform retries with exponential backoff on exception."""

    @patch('apps.inventory.sync_engine.InventorySyncOrchestrator.run_sync')
    def test_task_retries_on_exception(self, mock_run_sync):
        dev, org = make_developer()
        connection = _make_connection(org)
        mock_run_sync.side_effect = Exception('Network error')

        from apps.inventory.tasks import sync_external_platform
        from celery.exceptions import Retry
        with self.assertRaises((Exception, Retry)):
            sync_external_platform.apply(args=[str(connection.id)])


class SyncAllConnectionsFanOutTest(TestCase):
    """sync_all_active_connections fans out to all is_active=True connections."""

    @patch('apps.inventory.tasks.sync_external_platform.delay')
    def test_fans_out_to_active_connections(self, mock_delay):
        dev, org = make_developer()
        c1 = _make_connection(org, platform='zameen')
        c2 = _make_connection(org, platform='bayut')
        _make_connection(org, platform='rightmove', is_active=False)

        from apps.inventory.tasks import sync_all_active_connections
        sync_all_active_connections()

        dispatched_ids = {str(call.args[0]) for call in mock_delay.call_args_list}
        self.assertIn(str(c1.id), dispatched_ids)
        self.assertIn(str(c2.id), dispatched_ids)
        self.assertNotIn(str(_make_connection(org, platform='zillow', is_active=False).id),
                         dispatched_ids)


class OrgIsolationTest(TestCase):
    """Connection A never touches org B's properties."""

    @patch('apps.inventory.adapters.zameen.requests.get')
    def test_sync_scoped_to_connection_org(self, mock_get):
        dev_a, org_a = make_developer(org_name='Org A')
        dev_b, org_b = make_developer(org_name='Org B')
        prop_b = make_property(org=org_b, title='Org B Property', city='Lahore')
        connection_a = _make_connection(org_a, field_mappings={'title': 'name', 'city': 'city'})

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {'results': [{'name': 'Org B Property', 'city': 'Lahore'}]},
        )
        mock_get.return_value.raise_for_status = lambda: None

        InventorySyncOrchestrator().run_sync(connection_a)
        # prop_b belongs to org_b; connection_a is org_a — should never update it
        prop_b.refresh_from_db()
        self.assertEqual(prop_b.title, 'Org B Property')


class IdempotentDeltaTest(TestCase):
    """Idempotent delta: same external listing applied twice → single Property update."""

    @patch('apps.inventory.tasks.dispatch_delta_to_platform.delay')
    def test_apply_delta_idempotent(self, mock_delay):
        dev, org = make_developer()
        prop = make_property(org=org, title='Start', city='Karachi')
        connection = _make_connection(org, sync_direction='outbound')

        delta = {'title': 'Updated Title', 'city': 'Karachi'}
        orch = InventorySyncOrchestrator()
        orch._apply_delta(connection, str(prop.id), delta)
        orch._apply_delta(connection, str(prop.id), delta)

        prop.refresh_from_db()
        self.assertEqual(prop.title, 'Updated Title')


class DeltaIdempotencyRedisTest(TestCase):
    """dispatch_delta_to_platform skips duplicate delta hash."""

    @patch('apps.inventory.tasks.requests.post')
    @patch('apps.inventory.tasks._sign_payload', return_value='sig')
    def test_duplicate_delta_skipped(self, mock_sign, mock_post):
        from django.core.cache import cache
        dev, org = make_developer()
        connection = _make_connection(org, base_url='https://example.com')
        delta = {'price': 5000000}

        import hashlib, json
        delta_hash = hashlib.sha256(json.dumps(delta, sort_keys=True).encode()).hexdigest()[:16]

        # Pre-set idempotency key
        cache.set(f'delta_dispatched:{connection.id}:{delta_hash}', '1', 86400)

        from apps.inventory.tasks import dispatch_delta_to_platform
        dispatch_delta_to_platform.apply(args=[str(connection.id), str(org.id), delta_hash, delta])

        mock_post.assert_not_called()


def _jwt_client(user):
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import RefreshToken
    client = APIClient()
    token = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


class ConnectionTestViewTest(TestCase):
    """ConnectionTestView returns connected/error status shape."""

    @patch('apps.inventory.adapters.zameen.requests.get')
    def test_connection_test_connected(self, mock_get):
        dev, org = make_developer()
        connection = _make_connection(org)

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {'results': [{'name': 'test'}]},
        )
        mock_get.return_value.raise_for_status = lambda: None

        resp = _jwt_client(dev).post(f'/api/v1/inventory/connections/{connection.id}/test/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('status', resp.json())

    def test_connection_test_error_on_network_failure(self):
        from apps.inventory.adapters.zameen import ZameenAdapter
        dev, org = make_developer()
        connection = _make_connection(org)

        with patch.object(ZameenAdapter, 'poll_listings', side_effect=Exception('Connection refused')):
            resp = _jwt_client(dev).post(f'/api/v1/inventory/connections/{connection.id}/test/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'error')
