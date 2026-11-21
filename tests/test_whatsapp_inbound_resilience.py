"""T-1020 / KI-019: inbound WhatsApp messages survive DB connection starvation."""
import hashlib
import hmac
import json
from io import StringIO
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry
from django.core.cache import cache
from django.core.management import call_command
from django.db import InterfaceError, OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.whatsapp import inbound_resilience as ir
from apps.whatsapp.tasks import INBOUND_MAX_RETRIES, process_incoming_whatsapp_task

MSG = {'id': 'wamid.resilience.1', 'from': '923001234567', 'type': 'text', 'text': {'body': 'Hi'}}
ROUTE = 'apps.whatsapp.router.MessageRouter.route'


def _clear_dead_letters():
    ir._redis().delete(ir.DEAD_LETTER_KEY)


def _dead_letters():
    return [json.loads(r) for r in ir._redis().lrange(ir.DEAD_LETTER_KEY, 0, -1)]


@patch('apps.whatsapp.client.WhatsAppClient.mark_read')
class InboundRetryTest(TestCase):

    def setUp(self):
        _clear_dead_letters()
        self.addCleanup(_clear_dead_letters)

    def _run(self):
        """Act as the worker: re-deliver the task after each Retry, like the broker would."""
        self.countdowns = []
        for attempt in range(INBOUND_MAX_RETRIES + 2):
            try:
                return process_incoming_whatsapp_task.apply(args=[MSG, 'pnid-x'], retries=attempt)
            except Retry as r:
                self.countdowns.append(r.when)
        self.fail('task kept retrying past max_retries')

    def test_transient_db_error_is_retried_then_processed(self, _mr):
        with patch(ROUTE, side_effect=[OperationalError('too many connections'), None]) as route:
            self._run()
        self.assertEqual(route.call_count, 2)
        self.assertEqual(_dead_letters(), [])

    def test_interface_error_is_retried(self, _mr):
        with patch(ROUTE, side_effect=[InterfaceError('connection already closed'), None]) as route:
            self._run()
        self.assertEqual(route.call_count, 2)

    def test_exhausted_retries_dead_letter_the_message(self, _mr):
        with patch(ROUTE, side_effect=OperationalError('db down')) as route:
            self._run()
        self.assertEqual(route.call_count, INBOUND_MAX_RETRIES + 1)
        self.assertEqual(self.countdowns, [5, 10, 20, 40, 80, 160])
        letters = _dead_letters()
        self.assertEqual(len(letters), 1)
        self.assertEqual(letters[0]['message'], MSG)
        self.assertEqual(letters[0]['phone_number_id'], 'pnid-x')
        self.assertIn('OperationalError', letters[0]['error'])

    def test_no_retry_after_a_reply_was_sent(self, _mr):
        def send_then_fail(*a, **kw):
            ir.mark_side_effect()
            raise OperationalError('db died mid-flow')

        with patch(ROUTE, side_effect=send_then_fail) as route:
            self._run()
        self.assertEqual(route.call_count, 1)
        self.assertEqual(self.countdowns, [])
        self.assertEqual(_dead_letters(), [])

    def test_non_db_errors_are_not_retried(self, _mr):
        with patch(ROUTE, side_effect=RuntimeError('bug')) as route:
            self._run()
        self.assertEqual(route.call_count, 1)
        self.assertEqual(_dead_letters(), [])

    def test_org_lookup_db_error_retries_instead_of_using_global_creds(self, _mr):
        from apps.organizations.models import Organization
        real_filter = Organization.objects.filter
        calls = {'n': 0}

        def flaky_filter(*a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise OperationalError('pool exhausted')
            return real_filter(*a, **kw)

        with patch.object(Organization.objects, 'filter', side_effect=flaky_filter), \
             patch(ROUTE) as route:
            self._run()
        self.assertGreaterEqual(calls['n'], 2)
        route.assert_called_once()


class SideEffectTrackingTest(TestCase):

    def test_client_sends_are_tracked(self):
        from apps.whatsapp.client import WhatsAppClient
        client = WhatsAppClient('tok', 'pnid')
        with patch('apps.whatsapp.client.requests.post') as post, \
             patch('apps.resilience.resilience_engine.meta_cloud_api_circuit.call', return_value={}):
            post.return_value = MagicMock(json=lambda: {})
            with ir.track_side_effects() as fx:
                client.send_text('923001234567', 'hello', skip_window_check=True)
                client.send_template('923001234567', 'tpl')
            self.assertEqual(fx.count, 2)

    def test_mark_read_is_not_a_side_effect(self):
        from apps.whatsapp.client import WhatsAppClient
        with patch('apps.whatsapp.client.requests.post'):
            with ir.track_side_effects() as fx:
                WhatsAppClient('tok', 'pnid').mark_read('wamid.1')
        self.assertEqual(fx.count, 0)

    def test_mark_outside_tracking_is_noop(self):
        ir.mark_side_effect()  # must not raise

    def test_backoff(self):
        self.assertEqual([ir.retry_countdown(n) for n in range(7)], [5, 10, 20, 40, 80, 160, 300])


class DeadLetterReplayTest(TestCase):

    def setUp(self):
        _clear_dead_letters()
        self.addCleanup(_clear_dead_letters)

    def test_replay_reenqueues_oldest_first(self):
        ir.dead_letter({'id': 'a'}, 'p1', '', OperationalError('x'))
        ir.dead_letter({'id': 'b'}, 'p2', '', OperationalError('x'))
        with patch('apps.whatsapp.tasks.process_incoming_whatsapp_task.delay') as delay:
            self.assertEqual(ir.replay_dead_letters(), 2)
        self.assertEqual([c.args[0]['id'] for c in delay.call_args_list], ['a', 'b'])
        self.assertEqual(ir.dead_letter_count(), 0)

    def test_replay_keeps_record_if_broker_fails(self):
        ir.dead_letter({'id': 'a'}, 'p1', '', OperationalError('x'))
        with patch('apps.whatsapp.tasks.process_incoming_whatsapp_task.delay', side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                ir.replay_dead_letters()
        self.assertEqual(ir.dead_letter_count(), 1)

    def test_dead_letter_reports_redis_failure(self):
        with patch('apps.whatsapp.inbound_resilience._redis', side_effect=ConnectionError):
            self.assertFalse(ir.dead_letter({'id': 'a'}, 'p', '', OperationalError('x')))

    def test_management_command(self):
        ir.dead_letter({'id': 'a'}, 'p1', '', OperationalError('x'))
        out = StringIO()
        call_command('replay_whatsapp_dead_letters', '--count', stdout=out)
        self.assertEqual(out.getvalue().strip(), '1')
        with patch('apps.whatsapp.tasks.process_incoming_whatsapp_task.delay'):
            out = StringIO()
            call_command('replay_whatsapp_dead_letters', stdout=out)
        self.assertIn('Re-enqueued 1', out.getvalue())


@override_settings(WA_APP_SECRET='test-secret')
class WebhookDispatchFailureTest(TestCase):

    def setUp(self):
        cache.clear()

    def _post(self):
        body = json.dumps({'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'pnid-x'}, 'messages': [MSG]}}]}]}).encode()
        sig = 'sha256=' + hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()
        client = self.client_class(raise_request_exception=False)
        return client.post(reverse('wa-webhook'), data=body, content_type='application/json',
                           HTTP_X_HUB_SIGNATURE_256=sig)

    def test_broker_failure_releases_idempotency_key_for_meta_retry(self):
        with patch('apps.whatsapp.tasks.process_incoming_whatsapp_task.delay', side_effect=ConnectionError('broker down')):
            resp = self._post()
        self.assertEqual(resp.status_code, 500)
        self.assertIsNone(cache.get(f"wa:processed:{MSG['id']}"))

        with patch('apps.whatsapp.tasks.process_incoming_whatsapp_task.delay') as delay:
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        delay.assert_called_once()

    def test_successful_dispatch_keeps_key(self):
        with patch('apps.whatsapp.tasks.process_incoming_whatsapp_task.delay'):
            self._post()
        self.assertTrue(cache.get(f"wa:processed:{MSG['id']}"))
