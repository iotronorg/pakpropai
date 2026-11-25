"""MVP criteria 4 and 14: notification delivery measured; SLO metrics, rules and dashboards (ADR-050)."""
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from celery.exceptions import Retry
from django.conf import settings
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import get_resolver, reverse
from django.utils import timezone
from prometheus_client import REGISTRY

from apps.core import outbox, slo
from apps.core.metrics import ai_request_duration_seconds, metrics_registry, notification_delivery_seconds
from apps.core.models import OutboxMessage
from apps.core.views import _scraper_allowed
from apps.notifications.models import Notification, UserNotificationPreference
from apps.notifications.services import notify_user
from tests.factories import make_user
from tests.test_runtime import LOCMEM

ROOT = Path(settings.BASE_DIR)
RULES = (ROOT / 'ops/prometheus-alerts.yaml').read_text()
WA_OK = {'messages': [{'id': 'wamid.slo'}]}


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def delivered(event, channel):
    return sample('realtron_notification_delivery_seconds_count', event=event, channel=channel)


def failures(event, channel, reason):
    return sample('realtron_notification_delivery_failures_total', event=event, channel=channel, reason=reason)


def wa_client():
    client = MagicMock()
    client.send_text.return_value = WA_OK
    return patch('apps.whatsapp.client.get_wa_client', return_value=client)


# ── Delivery measurement (criterion 4) ────────────────────────────────────────

@override_settings(CACHES=LOCMEM, EMAIL_HOST='smtp.test')
class DeliveryMetricTest(TestCase):
    def setUp(self):
        self.user = make_user(email='agent@agency.example')

    def notify(self, client=None, **kw):
        """Celery is eager in tests: the outbox delivers when the transaction commits."""
        if client is None:
            client = MagicMock()
            client.send_text.return_value = WA_OK
        with patch('apps.whatsapp.client.get_wa_client', return_value=client), \
                self.captureOnCommitCallbacks(execute=True):
            notify_user(self.user, title='New enquiry', message='m', send_email=True, event='enquiry.new', **kw)
        return Notification.objects.filter(user=self.user).latest('created_at')

    def test_each_channel_is_timed_from_the_notification(self):
        before = [delivered('enquiry.new', c) for c in ('in_app', 'whatsapp', 'email')]
        n = self.notify()
        self.assertEqual(n.event, 'enquiry.new')
        self.assertEqual(set(OutboxMessage.objects.values_list('status', flat=True)), {'sent'})
        self.assertEqual([delivered('enquiry.new', c) for c in ('in_app', 'whatsapp', 'email')],
                         [before[0] + 1, before[1] + 1, before[2] + 1])

    def test_latency_lands_in_the_right_bucket(self):
        with patch('apps.core.outbox._schedule'):           # hold the delivery back
            n = self.notify()
        Notification.objects.filter(pk=n.pk).update(created_at=timezone.now() - timedelta(seconds=75))
        le60 = sample('realtron_notification_delivery_seconds_bucket', event='enquiry.new', channel='whatsapp', le='60.0')
        le90 = sample('realtron_notification_delivery_seconds_bucket', event='enquiry.new', channel='whatsapp', le='90.0')
        with wa_client():
            outbox.dispatch(OutboxMessage.objects.get(kind='notification.whatsapp').pk)
        self.assertEqual(sample('realtron_notification_delivery_seconds_bucket', event='enquiry.new',
                                channel='whatsapp', le='60.0'), le60)        # late: not under 60 s
        self.assertEqual(sample('realtron_notification_delivery_seconds_bucket', event='enquiry.new',
                                channel='whatsapp', le='90.0'), le90 + 1)

    def test_failures_are_counted_by_reason(self):
        perm, exh = failures('enquiry.new', 'whatsapp', 'permanent'), failures('enquiry.new', 'whatsapp', 'exhausted')
        client = MagicMock()
        client.send_text.side_effect = ValueError('outside the 24h window')
        self.notify(client)
        self.assertEqual(failures('enquiry.new', 'whatsapp', 'permanent'), perm + 1)

        with patch('apps.core.outbox._schedule'):
            self.notify()
        row = OutboxMessage.objects.filter(kind='notification.whatsapp', status='pending').get()
        OutboxMessage.objects.filter(pk=row.pk).update(max_attempts=1)
        client.send_text.side_effect = ConnectionError('meta down')
        with patch('apps.whatsapp.client.get_wa_client', return_value=client):
            self.assertEqual(outbox.dispatch(row.pk), 'failed')
        self.assertEqual(failures('enquiry.new', 'whatsapp', 'exhausted'), exh + 1)

    def test_preferences_block_is_not_a_delivery_failure(self):
        UserNotificationPreference.objects.update_or_create(user=self.user, defaults={'whatsapp_enabled': False})
        before = failures('enquiry.new', 'whatsapp', 'preferences')
        self.notify()
        self.assertEqual(failures('enquiry.new', 'whatsapp', 'preferences'), before + 1)
        self.assertNotIn('preferences', re.findall(r'reason=~"([^"]+)"', RULES)[0])

    def test_unknown_events_share_one_series(self):
        before = delivered('other', 'in_app')
        with self.captureOnCommitCallbacks(execute=True):
            notify_user(self.user, title='t', message='m', send_whatsapp=False, event='x' * 30)
            notify_user(self.user, title='t', message='m', send_whatsapp=False)
        self.assertEqual(delivered('other', 'in_app'), before + 2)

    def test_outbox_outcomes_are_counted(self):
        before = sample('realtron_outbox_deliveries_total', kind='notification.whatsapp', result='retry')
        sent = sample('realtron_outbox_deliveries_total', kind='notification.email', result='sent')
        client = MagicMock()
        client.send_text.side_effect = ConnectionError('meta down')
        with patch('apps.core.outbox.backoff_seconds', return_value=0), \
                patch('apps.core.tasks.dispatch_outbox_message.apply_async'):   # no eager re-dispatch
            self.notify(client)
        self.assertEqual(sample('realtron_outbox_deliveries_total', kind='notification.whatsapp', result='retry'),
                         before)
        row = OutboxMessage.objects.get(kind='notification.whatsapp')
        with patch('apps.whatsapp.client.get_wa_client', return_value=client), \
                patch('apps.core.outbox._schedule'):
            self.assertEqual(outbox.dispatch(row.pk), 'pending')
        self.assertEqual(sample('realtron_outbox_deliveries_total', kind='notification.whatsapp', result='retry'),
                         before + 1)
        outbox.dispatch(OutboxMessage.objects.get(kind='notification.email').pk)
        self.assertEqual(sample('realtron_outbox_deliveries_total', kind='notification.email', result='sent'), sent + 1)


@override_settings(CACHES=LOCMEM, EMAIL_HOST='smtp.test', FRONTEND_URL='https://app.test')
class EnquiryEventTest(TestCase):
    """The enquiry flow names its notifications, so the report and the SLO can find them."""

    def test_new_message_and_reply_events(self):
        from tests.test_enquiries import Base
        base = Base(methodName='setUp')
        base.setUp()
        e = base.thread()
        self.assertEqual(list(Notification.objects.filter(user=base.owner).values_list('event', flat=True)),
                         ['enquiry.new'])
        from apps.enquiries import services as S
        S.mark_read(e, 'responder')
        base.say(base.buyer, e, 'again')
        self.assertEqual(set(Notification.objects.filter(user=base.owner).values_list('event', flat=True)),
                         {'enquiry.new', 'enquiry.message'})
        base.say(base.owner, e, 'Yes, still available')
        self.assertEqual(list(Notification.objects.filter(user=base.buyer).values_list('event', flat=True)),
                         ['enquiry.reply'])


@override_settings(CACHES=LOCMEM)
class DeliveryReportTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.now = timezone.now()

    def note(self, age_s, event='enquiry.new'):
        n = Notification.objects.create(user=self.user, title='t', message='m', event=event)
        Notification.objects.filter(pk=n.pk).update(created_at=self.now - timedelta(seconds=age_s))
        n.refresh_from_db()
        return n

    def row(self, key, status, sent_after=None):
        OutboxMessage.objects.create(kind='notification.whatsapp' if key.startswith('notification') else
                                     'notification.email', idempotency_key=key, status=status,
                                     available_at=self.now, payload='{}')
        if sent_after is not None:
            n = Notification.objects.get(pk=key.split(':')[1])
            OutboxMessage.objects.filter(idempotency_key=key).update(
                sent_at=n.created_at + timedelta(seconds=sent_after))

    def build(self):
        on_time = [self.note(600) for _ in range(3)]
        late, failed, stuck, fresh = self.note(600), self.note(600), self.note(300), self.note(5)
        for n, s in zip(on_time, (2, 10, 50)):
            self.row(f'notification:{n.pk}', 'sent', s)
        self.row(f'notification:{late.pk}', 'sent', 95)
        self.row(f'notification:{failed.pk}', 'failed')
        self.row(f'notification:{stuck.pk}', 'pending')     # older than 60 s: late
        self.row(f'notification:{fresh.pk}', 'pending')     # still inside the minute
        self.row(f'email:{on_time[0].pk}', 'sent', 30)
        self.note(10, event='saved_search.alert')            # not an enquiry
        old = self.note(3 * 86400)                           # outside the window
        self.row(f'notification:{old.pk}', 'sent', 1)

    def test_report_per_channel(self):
        self.build()
        r = slo.delivery_report(self.now - timedelta(hours=24), self.now + timedelta(seconds=1))
        ch = {c['channel']: c for c in r['channels']}
        self.assertEqual((ch['in_app']['total'], ch['in_app']['within_target']), (7, 7))
        wa = ch['whatsapp']
        self.assertEqual({k: wa[k] for k in ('total', 'delivered', 'within_target', 'late_pending', 'pending', 'failed')},
                         {'total': 7, 'delivered': 4, 'within_target': 3, 'late_pending': 1, 'pending': 1, 'failed': 1})
        self.assertEqual(wa['ratio_within_target'], 0.5)       # 3 of the 6 whose outcome is known
        self.assertIs(wa['meets_objective'], False)
        self.assertEqual((wa['p50_s'], wa['max_s']), (10.0, 95.0))
        self.assertEqual((ch['email']['total'], ch['email']['ratio_within_target'], ch['email']['meets_objective']),
                         (1, 1.0, True))
        self.assertEqual(r['notes'], [])
        self.assertTrue(slo.delivery_report(self.now - timedelta(days=8))['notes'])

    def test_command(self):
        self.build()
        out = StringIO()
        call_command('notification_delivery_report', stdout=out)
        self.assertIn('whatsapp total=7 delivered=4 within=3 (50.00%) late_pending=1', out.getvalue())
        out = StringIO()
        call_command('notification_delivery_report', '--json', stdout=out)
        self.assertEqual(json.loads(out.getvalue())['target_s'], 60.0)
        with self.assertRaisesMessage(CommandError, 'whatsapp'):
            call_command('notification_delivery_report', '--fail-under-objective', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('notification_delivery_report', '--hours', '0', stdout=StringIO())

    def test_no_data_is_not_a_failure(self):
        call_command('notification_delivery_report', '--fail-under-objective', stdout=StringIO())


# ── Webhook processing ────────────────────────────────────────────────────────

MSG = {'id': 'wamid.slo.1', 'from': '923001234567', 'type': 'text', 'text': {'body': 'Hi'}}


@override_settings(WA_APP_SECRET='test-secret', CACHES=LOCMEM)
class WebhookMetricTest(TestCase):
    def setUp(self):
        cache.clear()

    def post(self, body=None, secret=b'test-secret'):
        body = body if body is not None else json.dumps({'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': 'pnid-x'}, 'messages': [MSG]}}]}]}).encode()
        sig = 'sha256=' + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return self.client.post(reverse('wa-webhook'), data=body, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256=sig)

    def edge(self, result):
        return sample('realtron_webhook_events_total', gateway='whatsapp', result=result)

    def test_edge_outcomes(self):
        before = {r: self.edge(r) for r in ('ok', 'duplicate', 'invalid_sig', 'bad_payload')}
        with patch('apps.whatsapp.tasks.process_incoming_whatsapp_task.delay') as delay:
            self.assertEqual(self.post().status_code, 200)
            self.assertEqual(self.post().status_code, 200)
            self.assertEqual(self.post(secret=b'wrong').status_code, 403)
            self.assertEqual(self.post(body=b'not json').status_code, 400)
        delay.assert_called_once()
        self.assertAlmostEqual(delay.call_args.kwargs['received_at'], time.time(), delta=30)
        self.assertEqual({r: self.edge(r) - before[r] for r in before},
                         {'ok': 1, 'duplicate': 1, 'invalid_sig': 1, 'bad_payload': 1})


@patch('apps.whatsapp.client.WhatsAppClient.mark_read')
class InboundTaskMetricTest(TestCase):
    def processed(self, result):
        return sample('realtron_webhook_processed_total', gateway='whatsapp', result=result)

    def test_queue_delay_and_ok(self, _mr):
        from apps.whatsapp.tasks import process_incoming_whatsapp_task
        le5 = sample('realtron_webhook_queue_delay_seconds_bucket', gateway='whatsapp', le='5.0')
        le30 = sample('realtron_webhook_queue_delay_seconds_bucket', gateway='whatsapp', le='30.0')
        ok = self.processed('ok')
        with patch('apps.whatsapp.router.MessageRouter.route'):
            process_incoming_whatsapp_task.apply(args=[MSG, 'pnid-x'], kwargs={'received_at': time.time() - 12})
        self.assertEqual(sample('realtron_webhook_queue_delay_seconds_bucket', gateway='whatsapp', le='5.0'), le5)
        self.assertEqual(sample('realtron_webhook_queue_delay_seconds_bucket', gateway='whatsapp', le='30.0'), le30 + 1)
        self.assertEqual(self.processed('ok'), ok + 1)

    def test_old_style_call_without_receipt_time(self, _mr):
        from apps.whatsapp.tasks import process_incoming_whatsapp_task
        count = sample('realtron_webhook_queue_delay_seconds_count', gateway='whatsapp')
        with patch('apps.whatsapp.router.MessageRouter.route'):
            process_incoming_whatsapp_task.apply(args=[MSG, 'pnid-x'])
        self.assertEqual(sample('realtron_webhook_queue_delay_seconds_count', gateway='whatsapp'), count)

    def test_router_crash_is_an_error(self, _mr):
        from apps.whatsapp.tasks import process_incoming_whatsapp_task
        err = self.processed('error')
        with patch('apps.whatsapp.router.MessageRouter.route', side_effect=RuntimeError('boom')):
            process_incoming_whatsapp_task.apply(args=[MSG, 'pnid-x'])
        self.assertEqual(self.processed('error'), err + 1)

    def test_retry_then_dead_letter(self, _mr):
        from apps.whatsapp import inbound_resilience as ir
        from apps.whatsapp.tasks import INBOUND_MAX_RETRIES, process_incoming_whatsapp_task
        ir._redis().delete(ir.DEAD_LETTER_KEY)
        self.addCleanup(lambda: ir._redis().delete(ir.DEAD_LETTER_KEY))
        retry, dead, count = (self.processed('retry'), self.processed('dead_letter'),
                              sample('realtron_webhook_queue_delay_seconds_count', gateway='whatsapp'))
        with patch('apps.whatsapp.router.MessageRouter.route', side_effect=OperationalError('db down')):
            for attempt in range(INBOUND_MAX_RETRIES + 1):
                try:
                    process_incoming_whatsapp_task.apply(args=[MSG, 'pnid-x'], retries=attempt,
                                                         kwargs={'received_at': time.time()})
                except Retry:
                    pass
        self.assertEqual(self.processed('retry'), retry + INBOUND_MAX_RETRIES)
        self.assertEqual(self.processed('dead_letter'), dead + 1)
        # Only the first attempt measures pickup delay; retries wait on purpose.
        self.assertEqual(sample('realtron_webhook_queue_delay_seconds_count', gateway='whatsapp'), count + 1)


# ── Export ────────────────────────────────────────────────────────────────────

@override_settings(CACHES=LOCMEM)
class MetricsEndpointTest(TestCase):
    def setUp(self):
        slo.outbox_backlog._at = 0.0

    def test_scraper_allow_list(self):
        self.assertTrue(_scraper_allowed('10.1.2.3', ['10.0.0.0/8']))
        self.assertTrue(_scraper_allowed('127.0.0.1', ['127.0.0.1', '::1']))
        self.assertTrue(_scraper_allowed('203.0.113.9', ['0.0.0.0']))
        self.assertFalse(_scraper_allowed('203.0.113.9', ['10.0.0.0/8', 'junk']))
        self.assertFalse(_scraper_allowed('', ['10.0.0.0/8']))
        with override_settings(PROMETHEUS_ALLOWED_IPS=['10.0.0.0/8']):
            self.assertEqual(self.client.get('/metrics/', REMOTE_ADDR='192.168.1.1').status_code, 403)
            self.assertEqual(self.client.get('/metrics/', REMOTE_ADDR='10.9.9.9').status_code, 200)

    def test_backlog_from_the_database(self):
        old = timezone.now() - timedelta(minutes=5)
        for i, status in enumerate(['pending', 'sending', 'sent']):
            m = OutboxMessage.objects.create(kind='notification.email', idempotency_key=f'k{i}', status=status,
                                             available_at=old, payload='{}')
            OutboxMessage.objects.filter(pk=m.pk).update(created_at=old)
        body = self.client.get('/metrics/', REMOTE_ADDR='127.0.0.1').content.decode()
        self.assertIn('realtron_outbox_undelivered{kind="notification.email"} 2.0', body)
        self.assertIn('realtron_outbox_undelivered{kind="whatsapp.otp"} 0.0', body)
        age = float(re.search(r'realtron_outbox_oldest_undelivered_seconds\{kind="notification.email"\} (\S+)', body)[1])
        self.assertGreaterEqual(age, 299)
        self.assertIn('django_http_requests_total_by_method_total', body)    # default registry still exported

    def test_backlog_is_cached_and_survives_db_errors(self):
        c = slo.OutboxBacklogCollector(ttl=60)
        with self.assertNumQueries(1):
            list(c.collect())
            list(c.collect())
        c = slo.OutboxBacklogCollector()
        with patch.object(c, '_read', side_effect=OperationalError('down')):
            families = list(c.collect())
        self.assertEqual([len(f.samples) for f in families], [len(outbox.HANDLERS)] * 2)   # zeros, no crash

    def test_default_registry_without_extras(self):
        self.assertIs(metrics_registry(), REGISTRY)


class MultiprocessExportTest(SimpleTestCase):
    """Samples from several processes add up (gunicorn workers, Celery prefork pool)."""

    def test_samples_from_every_process_are_added(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, 'PROMETHEUS_MULTIPROC_DIR': d, 'PYTHONPATH': str(ROOT)}
            child = ("from apps.core.metrics import webhook_processed_total as m;"
                     "m.labels(gateway='whatsapp', result='ok').inc(3)")
            for _ in range(2):
                subprocess.run([sys.executable, '-c', child], env=env, check=True, cwd=ROOT)
            read = ("from prometheus_client import generate_latest;"
                    "from apps.core.metrics import metrics_registry;"
                    "print(generate_latest(metrics_registry()).decode())")
            out = subprocess.run([sys.executable, '-c', read], env=env, check=True, cwd=ROOT,
                                 capture_output=True, text=True).stdout
        self.assertIn('realtron_webhook_processed_total{gateway="whatsapp",result="ok"} 6.0', out)


class WorkerExporterTest(SimpleTestCase):
    def test_off_by_default(self):
        from config.celery import start_metrics_server
        with patch('prometheus_client.start_http_server') as start:
            self.assertIsNone(start_metrics_server())
        start.assert_not_called()

    @override_settings(WORKER_METRICS_PORT=9808)
    def test_serves_the_aggregate_registry(self):
        from config.celery import start_metrics_server
        with patch('prometheus_client.start_http_server') as start, \
                patch.dict(os.environ, {'PROMETHEUS_MULTIPROC_DIR': tempfile.gettempdir()}):
            self.assertEqual(start_metrics_server(), 9808)
        self.assertEqual(start.call_args.args, (9808,))
        self.assertIsNot(start.call_args.kwargs['registry'], REGISTRY)

    @override_settings(WORKER_METRICS_PORT=9808)
    def test_port_in_use_does_not_stop_the_worker(self):
        from config.celery import start_metrics_server
        with patch('prometheus_client.start_http_server', side_effect=OSError('in use')), \
                self.assertLogs('config.celery', 'WARNING'):
            self.assertIsNone(start_metrics_server())

    def test_dead_process_is_marked(self):
        from config.celery import mark_metrics_process_dead
        with patch('prometheus_client.multiprocess.mark_process_dead') as dead, \
                patch.dict(os.environ, {'PROMETHEUS_MULTIPROC_DIR': tempfile.gettempdir()}):
            mark_metrics_process_dead(pid=123)
        dead.assert_called_once_with(123)


# ── Rules and dashboards agree with the code ──────────────────────────────────

class RulesAgreeWithCodeTest(SimpleTestCase):
    def test_every_objective_has_its_target_and_burn_inputs(self):
        for name, (objective, threshold, _) in slo.OBJECTIVES.items():
            self.assertRegex(RULES, rf'vector\({objective:g}\),\s+labels: {{slo: {name}}}', name)
            for w in ('5m', '30m', '1h', '6h'):
                self.assertRegex(RULES, rf'record: slo:sli_error:ratio_rate{w}\n\s+labels: {{slo: {name}}}', name)
        self.assertEqual(len(re.findall(r'record: slo:objective:ratio', RULES)), len(slo.OBJECTIVES))

    def test_thresholds_are_histogram_buckets(self):
        from django_prometheus.conf import PROMETHEUS_LATENCY_BUCKETS
        self.assertIn(slo.OBJECTIVES['api_latency'][1], PROMETHEUS_LATENCY_BUCKETS)
        self.assertIn(slo.OBJECTIVES['search_latency'][1], PROMETHEUS_LATENCY_BUCKETS)
        self.assertIn(slo.OBJECTIVES['ai_reply_latency'][1], ai_request_duration_seconds._upper_bounds)
        self.assertIn(slo.OBJECTIVES['enquiry_notification'][1], notification_delivery_seconds._upper_bounds)
        for le in re.findall(r'le="([\d.]+)"', RULES):
            self.assertIn(le, {'0.3', '0.4', '8.0', '5.0', '60.0'})

    def test_rules_name_only_metrics_that_exist(self):
        from apps.core import metrics
        ours = {m._name for m in vars(metrics).values() if hasattr(m, '_name') and hasattr(m, '_type')}
        ours |= {'realtron_outbox_undelivered', 'realtron_outbox_oldest_undelivered_seconds'}
        dash = ''.join(p.read_text() for p in (ROOT / 'ops/grafana/dashboards').glob('*.json'))
        groups = set(re.findall(r'- name: (\S+)', RULES))
        # Quoted names are job labels (up{job="realtron_api"}), not metrics.
        for name in set(re.findall(r'(?<![\w"])(realtron_[a-z_]+)', RULES + dash)) - groups:
            base = re.sub(r'_(bucket|count|sum|total)$', '', name)
            self.assertTrue(name in ours or base in ours, name)

    def test_view_names_in_rules_are_real_url_names(self):
        names = {n for n in get_resolver().reverse_dict.keys() if isinstance(n, str)}
        for view in slo.SEARCH_VIEWS + slo.NON_REST_VIEWS:
            self.assertIn(view, names)
            self.assertIn(view, RULES)

    def test_dashboard_covers_every_objective(self):
        dash = json.loads((ROOT / 'ops/grafana/dashboards/realtron-slo.json').read_text())
        exprs = ' '.join(t['expr'] for p in dash['panels'] for t in p.get('targets', []))
        for name in slo.OBJECTIVES:
            self.assertIn(f'slo="{name}"', exprs)
        for p in dash['panels']:
            if p['type'] != 'row':
                self.assertEqual(p['datasource'], {'type': 'prometheus', 'uid': 'prometheus'})

    def test_scrape_config_has_api_and_workers(self):
        conf = (ROOT / 'ops/prometheus.yml').read_text()
        self.assertIn('job_name: realtron_api', conf)
        self.assertIn('job_name: realtron_worker', conf)
        self.assertIn('alertmanager:9093', conf)
