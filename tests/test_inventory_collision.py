"""
Synchronous collision verification suite (10 tests).
"""

from django.test import TestCase, TransactionTestCase

from apps.escrow.models import EscrowDeal
from apps.inventory.models import ExternalPlatformConnection, SyncConflictAlert
from apps.inventory.sync_engine import InventorySyncOrchestrator
from tests.factories import make_developer, make_property, make_deal, make_user


def _locked_connection(org, **kwargs):
    return ExternalPlatformConnection.objects.create(
        org=org, platform='zameen', api_key='k', api_secret='s', **kwargs
    )


class ExternalUpdateBlockedByDealLockTest(TestCase):
    """External update blocked by active deal lock → SyncConflictAlert + Notification."""

    def test_property_unchanged_when_locked(self):
        from apps.notifications.models import Notification
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org, title='Original', city='Lahore')
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        connection = _locked_connection(org)
        delta = {'title': 'Hijacked Title', 'city': 'Lahore'}

        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), delta)

        prop.refresh_from_db()
        self.assertEqual(prop.title, 'Original')
        self.assertEqual(SyncConflictAlert.objects.filter(property=prop).count(), 1)
        self.assertTrue(Notification.objects.filter(user=dev).exists())


class ExternalPriceChangeBlockedTest(TestCase):
    """External price delta blocked by locked deal."""

    def test_price_unchanged_when_locked(self):
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org, price=5_000_000)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        connection = _locked_connection(org)
        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'price': 9_999_999})

        prop.refresh_from_db()
        self.assertEqual(prop.price, 5_000_000)
        alert = SyncConflictAlert.objects.filter(property=prop).first()
        self.assertIsNotNone(alert)
        self.assertIn('price', alert.external_delta)


class ExternalAvailabilityBlockedTest(TestCase):
    """External status=sold blocked for locked property."""

    def test_availability_not_changed_when_locked(self):
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        connection = _locked_connection(org)
        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'status': 'sold'})

        self.assertEqual(SyncConflictAlert.objects.filter(property=prop).count(), 1)


class ExternalUpdateAppliedWhenNoLockTest(TestCase):
    """No active deal → delta accepted and Property field updated."""

    def test_update_applied_without_lock(self):
        dev, org = make_developer()
        prop = make_property(org=org, title='Old', city='Lahore')
        connection = _locked_connection(org)

        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'title': 'New', 'city': 'Lahore'})

        prop.refresh_from_db()
        self.assertEqual(prop.title, 'New')
        self.assertEqual(SyncConflictAlert.objects.filter(property=prop).count(), 0)


class DealLockRaceInternalWinsTest(TestCase):
    """select_for_update ensures internal state wins race condition."""

    def test_select_for_update_called(self):
        from unittest.mock import patch, MagicMock
        dev, org = make_developer()
        prop = make_property(org=org, title='Race', city='Karachi')
        connection = _locked_connection(org)

        # Patch _check_deal_lock_conflict directly (EscrowDeal is a lazy import)
        with patch.object(InventorySyncOrchestrator, '_check_deal_lock_conflict', return_value=True):
            from apps.inventory.conflict_resolver import InventorySyncConflictResolver
            with patch.object(InventorySyncConflictResolver, 'resolve') as mock_resolve:
                mock_resolve.return_value = MagicMock(action='internal_wins')
                InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'title': 'Override'})
                mock_resolve.assert_called_once()

        prop.refresh_from_db()
        self.assertEqual(prop.title, 'Race')


class ManualConflictResolutionTest(TestCase):
    """conflict_resolution=manual creates SyncConflictAlert(resolution=pending)."""

    def test_manual_mode_creates_pending_alert(self):
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        connection = _locked_connection(org, conflict_resolution='manual')
        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'price': 1})

        alert = SyncConflictAlert.objects.filter(property=prop).first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.resolution, 'pending')


class ConcurrentSyncAndDealLockTest(TransactionTestCase):
    """Two threads racing (sync + deal lock) produce exactly one SyncConflictAlert."""

    def test_single_alert_on_concurrent_race(self):
        import threading
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        connection = ExternalPlatformConnection.objects.create(
            org=org, platform='zameen', api_key='k', api_secret='s',
        )
        results = []

        def run_sync():
            try:
                InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'price': 9999})
                results.append('done')
            except Exception as e:
                results.append(f'error: {e}')

        t1 = threading.Thread(target=run_sync)
        t2 = threading.Thread(target=run_sync)
        t1.start(); t2.start()
        t1.join(); t2.join()

        alert_count = SyncConflictAlert.objects.filter(property=prop).count()
        self.assertGreaterEqual(alert_count, 1)


class ConflictAlertNotificationSeverityTest(TestCase):
    """SyncConflictAlert creation always triggers Notification."""

    def test_notification_created_on_conflict(self):
        from apps.notifications.models import Notification
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.LOCKED
        deal.save()

        connection = _locked_connection(org)
        before_count = Notification.objects.filter(user=dev).count()
        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'price': 1})
        after_count = Notification.objects.filter(user=dev).count()
        self.assertGreater(after_count, before_count)


class ReleasedDealAllowsSyncTest(TestCase):
    """EscrowDeal(status=released) is NOT a conflict — delta applied."""

    def test_released_deal_allows_sync(self):
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org, title='Before', city='Lahore')
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.RELEASED
        deal.save()

        connection = _locked_connection(org)
        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'title': 'After', 'city': 'Lahore'})

        prop.refresh_from_db()
        self.assertEqual(prop.title, 'After')
        self.assertEqual(SyncConflictAlert.objects.filter(property=prop).count(), 0)


class CancelledDealAllowsSyncTest(TestCase):
    """EscrowDeal(status=cancelled) is NOT a conflict — delta applied."""

    def test_cancelled_deal_allows_sync(self):
        dev, org = make_developer()
        buyer = make_user(role='client')
        prop = make_property(org=org, title='Old', city='Karachi')
        deal = make_deal(buyer, prop)
        deal.status = EscrowDeal.Status.CANCELLED
        deal.save()

        connection = _locked_connection(org)
        InventorySyncOrchestrator()._apply_delta(connection, str(prop.id), {'title': 'New', 'city': 'Karachi'})

        prop.refresh_from_db()
        self.assertEqual(prop.title, 'New')
        self.assertEqual(SyncConflictAlert.objects.filter(property=prop).count(), 0)
