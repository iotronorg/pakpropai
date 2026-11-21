"""
T-1021 / ADR-014: transactional outbox for outbound side effects, plus the
KI-033 regression (WhatsAppClient instance methods called on the class).
"""
import json
import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core import outbox
from apps.core.models import OutboxMessage
from apps.core.outbox import PermanentDeliveryError
from tests.factories import make_user

PHONE = '+923001234500'


def _meta_ok(msg_id='wamid.TEST'):
    resp = MagicMock()
    resp.json.return_value = {'messages': [{'id': msg_id}]}
    resp.raise_for_status.return_value = None
    return resp


class _Handler:
    """Swap a test handler into the registry."""

    def __init__(self, fn):
        self.fn = fn

    def __enter__(self):
        import tests.test_outbox as mod
        mod._CURRENT = self.fn
        self._p = patch.dict(outbox.HANDLERS, {'test.kind': 'tests.test_outbox._call_current'})
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()


_CURRENT = None


def _call_current(msg, payload):
    return _CURRENT(msg, payload)


def _enqueue(key='k1', **kw):
    return outbox.enqueue('test.kind', {'x': 1}, key=key, **kw)


class EnqueueTest(TestCase):
    def test_rolled_back_transaction_leaves_no_row(self):
        with _Handler(lambda m, p: 'ok'):
            try:
                with transaction.atomic():
                    _enqueue()
                    raise RuntimeError('business failure')
            except RuntimeError:
                pass
        self.assertFalse(OutboxMessage.objects.exists())

    def test_commit_schedules_dispatch_with_id_only(self):
        with _Handler(lambda m, p: 'ref-1'), \
                patch('apps.core.tasks.dispatch_outbox_message.apply_async') as aa:
            with self.captureOnCommitCallbacks(execute=True):
                msg = _enqueue()
        aa.assert_called_once()
        self.assertEqual(aa.call_args[0][0], (str(msg.pk),))
        self.assertEqual(aa.call_args[1]['queue'], 'default')

    def test_same_key_is_idempotent(self):
        with _Handler(lambda m, p: 'ok'):
            with self.captureOnCommitCallbacks() as callbacks:
                a = _enqueue('same')
                b = _enqueue('same')
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(len(callbacks), 1)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            outbox.enqueue('nope', {}, key='x')

    def test_payload_encrypted_at_rest(self):
        outbox.enqueue('whatsapp.otp', {'phone': PHONE, 'code': '918273'}, key='otp:enc')
        with connection.cursor() as cur:
            cur.execute("SELECT payload FROM outbox_messages WHERE idempotency_key='otp:enc'")
            raw = cur.fetchone()[0]
        self.assertTrue(raw.startswith('enc1:'))
        self.assertNotIn('918273', raw)

    def test_broker_down_does_not_raise(self):
        with _Handler(lambda m, p: 'ok'), \
                patch('apps.core.tasks.dispatch_outbox_message.apply_async',
                      side_effect=ConnectionError('redis down')):
            with self.captureOnCommitCallbacks(execute=True):
                msg = _enqueue()
        self.assertEqual(OutboxMessage.objects.get(pk=msg.pk).status, 'pending')
        self.assertIn(str(msg.pk), outbox.due_ids())


class DispatchTest(TestCase):
    def test_success_marks_sent(self):
        with _Handler(lambda m, p: 'wamid.1'):
            msg = _enqueue()
            self.assertEqual(outbox.dispatch(msg.pk), 'sent')
        msg.refresh_from_db()
        self.assertEqual((msg.status, msg.provider_ref, msg.attempts), ('sent', 'wamid.1', 1))
        self.assertIsNotNone(msg.sent_at)

    def test_second_dispatch_is_skipped(self):
        calls = []
        with _Handler(lambda m, p: calls.append(1)):
            msg = _enqueue()
            outbox.dispatch(msg.pk)
            self.assertEqual(outbox.dispatch(msg.pk), 'skipped')
        self.assertEqual(len(calls), 1)

    def test_permanent_error_fails_without_retry(self):
        def boom(m, p):
            raise PermanentDeliveryError('bad recipient')
        with _Handler(boom):
            msg = _enqueue()
            self.assertEqual(outbox.dispatch(msg.pk), 'failed')
        msg.refresh_from_db()
        self.assertEqual(msg.attempts, 1)
        self.assertIn('permanent', msg.last_error)

    def test_transient_error_backs_off_then_dies(self):
        def flaky(m, p):
            raise ConnectionError('timeout')
        with _Handler(flaky):
            msg = _enqueue(max_attempts=2)
            self.assertEqual(outbox.dispatch(msg.pk), 'pending')
            msg.refresh_from_db()
            self.assertGreater(msg.available_at, timezone.now())
            self.assertEqual(outbox.dispatch(msg.pk), 'skipped')  # not due yet
            OutboxMessage.objects.filter(pk=msg.pk).update(available_at=timezone.now())
            self.assertEqual(outbox.dispatch(msg.pk), 'failed')
        msg.refresh_from_db()
        self.assertEqual(msg.attempts, 2)
        self.assertIn('attempts exhausted', msg.last_error)

    def test_backoff_is_exponential_and_capped(self):
        self.assertEqual([outbox.backoff_seconds(n) for n in (1, 2, 3)], [15, 30, 60])
        self.assertEqual(outbox.backoff_seconds(30), outbox.BACKOFF_MAX_SECONDS)

    def test_expired_message_is_not_sent(self):
        calls = []
        with _Handler(lambda m, p: calls.append(1)):
            msg = _enqueue(expires_at=timezone.now() - timedelta(seconds=1))
            self.assertEqual(outbox.dispatch(msg.pk), 'expired')
        self.assertEqual(calls, [])

    def test_stale_lease_is_reclaimed(self):
        with _Handler(lambda m, p: 'ok'):
            msg = _enqueue()
            OutboxMessage.objects.filter(pk=msg.pk).update(
                status='sending', locked_until=timezone.now() - timedelta(seconds=1))
            self.assertIn(str(msg.pk), outbox.due_ids())
            self.assertEqual(outbox.dispatch(msg.pk), 'sent')

    def test_live_lease_is_respected(self):
        with _Handler(lambda m, p: 'ok'):
            msg = _enqueue()
            OutboxMessage.objects.filter(pk=msg.pk).update(
                status='sending', locked_until=timezone.now() + timedelta(minutes=1))
            self.assertNotIn(str(msg.pk), outbox.due_ids())
            self.assertEqual(outbox.dispatch(msg.pk), 'skipped')

    def test_sensitive_payload_wiped_after_send(self):
        with patch('apps.notifications.delivery.deliver_otp', return_value='w'), \
                patch.dict(outbox.HANDLERS, {'whatsapp.otp': 'apps.notifications.delivery.deliver_otp'}):
            msg = outbox.enqueue('whatsapp.otp', {'phone': PHONE, 'code': '111222'}, key='otp:wipe')
            outbox.dispatch(msg.pk)
        msg.refresh_from_db()
        self.assertEqual(msg.payload, '{}')


class RelayAndPurgeTest(TestCase):
    def test_relay_queues_only_due(self):
        with _Handler(lambda m, p: 'ok'):
            due = _enqueue('due')
            later = _enqueue('later')
            OutboxMessage.objects.filter(pk=later.pk).update(
                available_at=timezone.now() + timedelta(hours=1))
            with patch('apps.core.tasks.dispatch_outbox_message.apply_async') as aa:
                self.assertEqual(outbox.relay_due(), 1)
        self.assertEqual(aa.call_args[0][0], (str(due.pk),))

    def test_purge_keeps_recent_and_pending(self):
        with _Handler(lambda m, p: 'ok'):
            old_sent, old_failed, recent, pending = (_enqueue(k) for k in 'abcd')
        OutboxMessage.objects.filter(pk__in=[old_sent.pk, recent.pk]).update(status='sent')
        OutboxMessage.objects.filter(pk=old_failed.pk).update(status='failed')
        OutboxMessage.objects.filter(pk__in=[old_sent.pk, old_failed.pk, pending.pk]).update(
            created_at=timezone.now() - timedelta(days=40))
        self.assertEqual(outbox.purge(), 2)
        self.assertEqual(set(OutboxMessage.objects.values_list('pk', flat=True)),
                         {recent.pk, pending.pk})


@patch('apps.whatsapp.client.requests.post', return_value=_meta_ok('wamid.OTP'))
class OTPDeliveryTest(TestCase):
    """End to end through the real WhatsAppClient (only the HTTP call is patched)."""

    def test_otp_send_endpoint_delivers_via_outbox(self, post):
        with patch('apps.core.tasks.dispatch_outbox_message.apply_async',
                   wraps=None) as aa, self.captureOnCommitCallbacks(execute=True):
            aa.side_effect = lambda args, **kw: outbox.dispatch(args[0])
            r = APIClient().post('/api/v1/auth/otp/send/', {'phone': PHONE}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        msg = OutboxMessage.objects.get(kind='whatsapp.otp')
        self.assertEqual((msg.status, msg.provider_ref, msg.payload), ('sent', 'wamid.OTP', '{}'))
        # KI-029: the broker message carries only the outbox id.
        self.assertEqual(aa.call_args[0][0], (str(msg.pk),))
        body = post.call_args.kwargs['json']['text']['body']
        self.assertRegex(body, r'\*\d{6}\*')

    def test_legacy_send_otp_async_works_and_never_logs_code(self, post):
        from apps.notifications.tasks import send_otp_async
        with self.assertLogs('apps.notifications.tasks', level='INFO') as logs:
            send_otp_async.delay(PHONE, '445566')
        self.assertEqual(post.call_count, 1)
        self.assertNotIn('445566', '\n'.join(logs.output))


@patch('apps.whatsapp.client.is_within_24h_window', return_value=True)
@patch('apps.whatsapp.client.requests.post', return_value=_meta_ok('wamid.N'))
class NotificationDeliveryTest(TestCase):
    def setUp(self):
        self.user = make_user(phone=PHONE)

    def test_notify_user_delivers_through_real_client(self, post, _win):
        from apps.notifications.models import Notification
        from apps.notifications.services import notify_user
        with patch('apps.core.tasks.dispatch_outbox_message.apply_async') as aa, \
                self.captureOnCommitCallbacks(execute=True):
            aa.side_effect = lambda args, **kw: outbox.dispatch(args[0])
            notify_user(self.user, title='T', message='Hello there')
        n = Notification.objects.get(user=self.user)
        self.assertEqual((n.status, n.wa_message_id), ('sent', 'wamid.N'))
        self.assertEqual(post.call_args.kwargs['json']['text']['body'], 'Hello there')

    def test_already_sent_notification_is_not_resent(self, post, _win):
        from apps.notifications.delivery import enqueue_notification
        from apps.notifications.models import Notification
        n = Notification.objects.create(user=self.user, message='m', status='sent', wa_message_id='old')
        msg = enqueue_notification(n, retry=True)
        self.assertEqual(outbox.dispatch(msg.pk), 'sent')
        post.assert_not_called()

    def test_window_closed_is_permanent(self, post, win):
        from apps.notifications.delivery import enqueue_notification
        from apps.notifications.models import Notification
        win.return_value = False
        n = Notification.objects.create(user=self.user, message='m')
        msg = enqueue_notification(n)
        self.assertEqual(outbox.dispatch(msg.pk), 'failed')
        n.refresh_from_db()
        self.assertEqual(n.status, 'failed')
        post.assert_not_called()

    def test_final_attempt_marks_notification_failed(self, post, _win):
        from apps.notifications.delivery import enqueue_notification
        from apps.notifications.models import Notification
        post.side_effect = ConnectionError('meta down')
        n = Notification.objects.create(user=self.user, message='m')
        msg = enqueue_notification(n)
        OutboxMessage.objects.filter(pk=msg.pk).update(max_attempts=1)
        with patch('apps.resilience.resilience_engine.meta_cloud_api_circuit.call',
                   side_effect=lambda fn, fallback_fn=None: fn()):
            self.assertEqual(outbox.dispatch(msg.pk), 'failed')
        n.refresh_from_db()
        self.assertEqual(n.status, 'failed')

    def test_blocked_by_preferences_writes_no_outbox_row(self, post, _win):
        from apps.notifications.models import UserNotificationPreference
        from apps.notifications.services import notify_user
        UserNotificationPreference.objects.update_or_create(
            user=self.user, defaults={'whatsapp_enabled': False})
        notify_user(self.user, title='T', message='M')
        self.assertFalse(OutboxMessage.objects.exists())


class ClassLevelCallRegressionTest(TestCase):
    """KI-033: instance methods called on the class raised TypeError (swallowed)."""

    def test_no_class_level_calls_in_apps(self):
        root = Path(__file__).resolve().parent.parent / 'apps'
        pattern = re.compile(r'WhatsAppClient\.(send_\w+|mark_read|upload_media|download_media)\(')
        offenders = [
            f'{p.relative_to(root)}:{i}'
            for p in root.rglob('*.py')
            for i, line in enumerate(p.read_text().splitlines(), 1)
            if pattern.search(line) and not line.lstrip().startswith('#')
        ]
        self.assertEqual(offenders, [])

    @patch('apps.whatsapp.client.is_within_24h_window', return_value=True)
    @patch('apps.whatsapp.client.requests.post', return_value=_meta_ok())
    def test_hot_lead_alert_reaches_meta(self, post, _win):
        from apps.leads.signals import _fire_hot_lead_alert
        from tests.factories import make_agent, make_lead, make_org
        org = make_org()
        agent_user, agent = make_agent(org=org)
        lead = make_lead(make_user(), org=org)
        lead.assigned_agent = agent
        _fire_hot_lead_alert(lead)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs['json']['to'], agent_user.phone.lstrip('+'))
