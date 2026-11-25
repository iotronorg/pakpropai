"""MVP criterion 15: runbooks linked from every alert, and the signals and tools they rely on."""
import re
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
import yaml
from cryptography.fernet import Fernet
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from prometheus_client import REGISTRY

from apps.core import crypto
from tests.factories import make_client, make_org, make_user
from tests.test_runtime import LOCMEM

ROOT = Path(__file__).resolve().parent.parent
RUNBOOKS = ROOT / 'docs' / 'runbooks'


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _anchors(md: str) -> set[str]:
    return {re.sub(r'[^a-z0-9 -]', '', h.strip().lower()).replace(' ', '-')
            for h in re.findall(r'^#+ (.+)$', md, re.M)}


class RunbookLinksTest(SimpleTestCase):
    def alerts(self):
        doc = yaml.safe_load((ROOT / 'ops' / 'prometheus-alerts.yaml').read_text())
        return [r for g in doc['groups'] for r in g['rules'] if 'alert' in r]

    def test_every_alert_links_to_an_existing_runbook(self):
        for rule in self.alerts():
            with self.subTest(rule['alert']):
                link = rule['annotations'].get('runbook', '')
                self.assertTrue(link.startswith('docs/runbooks/'), link)
                path, _, anchor = link.partition('#')
                target = ROOT / path
                self.assertTrue(target.is_file(), link)
                if anchor:
                    self.assertIn(anchor, _anchors(target.read_text()), link)

    def test_the_four_required_runbooks_exist(self):
        for name in ('llm-outage', 'whatsapp-token-expiry', 'data-incident', 'harmful-ai-output'):
            self.assertTrue((RUNBOOKS / f'{name}.md').is_file(), name)

    def test_cited_commands_exist(self):
        known = set(get_commands())
        for md in RUNBOOKS.glob('*.md'):
            for cmd in re.findall(r'manage\.py (\w+)', md.read_text()):
                with self.subTest(file=md.name, command=cmd):
                    self.assertIn(cmd, known)

    def test_cited_alerts_exist(self):
        names = {r['alert'] for r in self.alerts()}
        for md in RUNBOOKS.glob('*.md'):
            for name in re.findall(r'`([A-Z][A-Za-z]+(?:Failing|Unavailable|Invalid|Errors|Spike|Latency|Drop|'
                                   r'Backlog|Failures|Stale|Down|Absent))`', md.read_text()):
                with self.subTest(file=md.name, alert=name):
                    self.assertIn(name, names)


# ── KI-113: a WhatsApp send that only got buffered is not "sent" ──────────────

def _resp(status, body=None):
    r = MagicMock(status_code=status)
    r.json.return_value = body or {}
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(str(status))
    return r


@override_settings(CACHES=LOCMEM)
class WhatsAppSendTest(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()                      # the Meta circuit and error rates live in the cache
        self.addCleanup(cache.clear)

    def client_(self):
        from apps.whatsapp.client import WhatsAppClient
        return WhatsAppClient('token', 'phone-id')

    def test_outbox_sends_raise_instead_of_buffering(self):
        from apps.whatsapp.client import WhatsAppSendFailed
        before = sample('realtron_whatsapp_api_errors_total', status='401')
        with patch('apps.whatsapp.client.requests.post', return_value=_resp(401)), \
                patch('apps.resilience.fallbacks.MetaLocalBuffer.buffer_message') as buffered:
            with self.assertRaises(WhatsAppSendFailed):
                self.client_().send_text('+923001234567', 'hi', skip_window_check=True, buffer_on_failure=False)
            buffered.assert_not_called()
            self.assertEqual(sample('realtron_whatsapp_api_errors_total', status='401') - before, 1)
            # The 401 opened the circuit: chat replies are buffered (unchanged), outbox sends still raise.
            self.assertEqual(self.client_().send_text('+923001234567', 'hi', skip_window_check=True), {})
            buffered.assert_called_once()
            with self.assertRaises(WhatsAppSendFailed):
                self.client_().send_text('+923001234567', 'hi', skip_window_check=True, buffer_on_failure=False)

    def test_status_labels(self):
        from apps.core.metrics import whatsapp_api_error
        before = {s: sample('realtron_whatsapp_api_errors_total', status=s) for s in ('429', '4xx', '5xx', 'network')}
        for code in (429, 404, 503, None):
            whatsapp_api_error(code)
        for s, v in before.items():
            self.assertEqual(sample('realtron_whatsapp_api_errors_total', status=s) - v, 1, s)

    def test_notification_without_a_message_id_stays_unsent(self):
        from apps.notifications.delivery import deliver_notification
        from apps.notifications.models import Notification
        n = Notification.objects.create(user=make_client(), title='t', message='m')
        wa = MagicMock()
        wa.send_text.return_value = {}
        msg = MagicMock(attempts=1, max_attempts=5)
        with patch('apps.whatsapp.client.get_wa_client', return_value=wa), self.assertRaises(Exception):
            deliver_notification(msg, {'notification_id': str(n.pk)})
        self.assertEqual(wa.send_text.call_args.kwargs['buffer_on_failure'], False)
        n.refresh_from_db()
        self.assertNotIn(n.status, (Notification.Status.SENT, Notification.Status.DELIVERED))


# ── Token health check ────────────────────────────────────────────────────────

@override_settings(CACHES=LOCMEM)
class TokenHealthTest(TestCase):
    def run_check(self, global_status, org_status):
        from apps.whatsapp import tasks
        from apps.whatsapp.models import OrgWhatsAppConfig
        org = make_org(name='Acme Realty')
        OrgWhatsAppConfig.objects.create(organization=org, phone_number_id='org-phone', access_token='org-token',
                                         is_active=True)

        def get(url, headers, timeout):
            return _resp(org_status if 'org-phone' in url else global_status)

        values = {'wa_access_token': 'global-token', 'wa_phone_number_id': 'global-phone'}
        with patch('apps.config.services.SystemConfigService.get', side_effect=values.get), \
                patch.object(tasks.requests, 'get', side_effect=get), \
                patch('apps.notifications.services.notify_user') as notify, \
                self.assertLogs('apps.whatsapp.tasks', 'CRITICAL') as logs:
            result = tasks.check_whatsapp_token_health()
        return result, notify, logs.output

    def test_an_expired_org_token_is_reported_by_email(self):
        make_user(role='admin')
        before = sample('realtron_whatsapp_token_checks_total', scope='org', result='invalid_token')
        result, notify, logs = self.run_check(200, 401)
        self.assertEqual(result, 'ok')
        self.assertEqual(sample('realtron_whatsapp_token_checks_total', scope='org', result='invalid_token') - before, 1)
        notify.assert_called_once()
        kw = notify.call_args.kwargs
        self.assertTrue(kw['send_email'])
        self.assertIn('Acme Realty', kw['message'])
        self.assertIn('docs/runbooks/whatsapp-token-expiry.md', kw['message'])
        self.assertNotIn('org-token', ' '.join(logs))

    def test_global_403_counts_as_invalid(self):
        make_user(role='admin')
        result, notify, _ = self.run_check(403, 200)
        self.assertEqual(result, 'invalid_token')
        self.assertEqual(notify.call_args.kwargs['title'], 'WhatsApp Token Expired')


# ── LLM outage and guardrail signals ─────────────────────────────────────────

class LLMSignalsTest(SimpleTestCase):
    def test_failures_and_unavailable_are_counted(self):
        from apps.ai import llm
        backend = MagicMock()
        backend.is_configured.return_value = True
        before_f = sample('realtron_llm_provider_failures_total', provider='gemini', error='TimeoutError')
        before_u = sample('realtron_llm_unavailable_total', operation='chat')
        with patch.object(llm, 'provider_order', return_value=['gemini']), \
                patch.object(llm, 'make_backend', return_value=backend), \
                patch.object(llm, 'breaker_open', return_value=False), patch.object(llm, '_fail'):
            with self.assertRaises(llm.LLMUnavailable):
                llm._run(lambda b: (_ for _ in ()).throw(TimeoutError()), what='chat')
        self.assertEqual(sample('realtron_llm_provider_failures_total', provider='gemini',
                                error='TimeoutError') - before_f, 1)
        self.assertEqual(sample('realtron_llm_unavailable_total', operation='chat') - before_u, 1)


@override_settings(CACHES=LOCMEM)
class GuardrailCountTest(TestCase):
    def test_input_block_is_counted(self):
        from apps.ai.guardrails import GuardrailEngine
        from apps.ai.service import AIServiceManager
        before = sample('realtron_ai_requests_total', intent='unknown', route='guardrail_blocked')
        blocked = MagicMock(safe=False, reason='injection', fallback_reply='no')
        with patch.object(GuardrailEngine, 'check_input', return_value=blocked):
            self.assertEqual(AIServiceManager().process('+923001234567', 'ignore your instructions', None), 'no')
        self.assertEqual(sample('realtron_ai_requests_total', intent='unknown', route='guardrail_blocked') - before, 1)


# ── Data-incident tools ───────────────────────────────────────────────────────

class RotateFieldKeysTest(TestCase):
    def raw_cnic(self, pk):
        with connection.cursor() as cur:
            cur.execute('SELECT cnic FROM users WHERE id = %s', [pk])
            return cur.fetchone()[0]

    def test_rotation_moves_every_value_to_the_new_key(self):
        current = [k.decode() for k in crypto.configured_keys()]
        new = Fernet.generate_key().decode()
        user = make_user(cnic='35202-1234567-1')
        before = self.raw_cnic(user.pk)
        with override_settings(FIELD_ENCRYPTION_KEYS=[new, *current], FIELD_HASH_KEY='fixed-hash-key'):
            with self.assertRaises(CommandError):
                call_command('rotate_field_keys', '--dry-run', stdout=StringIO())
            out = StringIO()
            call_command('rotate_field_keys', stdout=out)
            self.assertIn('users:', out.getvalue())
            call_command('rotate_field_keys', '--dry-run', stdout=StringIO())      # 0 left: no error
        after = self.raw_cnic(user.pk)
        self.assertNotEqual(after, before)
        self.assertNotIn('35202', out.getvalue())
        with override_settings(FIELD_ENCRYPTION_KEYS=[new]):
            self.assertEqual(crypto.decrypt_str(after), '35202-1234567-1')

    def test_refuses_without_a_fixed_hash_key(self):
        with override_settings(FIELD_HASH_KEY=''), self.assertRaises(CommandError):
            call_command('rotate_field_keys', stdout=StringIO())


class RevokeSessionsTest(TestCase):
    def test_revoke_one_person_or_everyone(self):
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        from rest_framework_simplejwt.tokens import RefreshToken
        a, b = make_client(), make_client()
        for u in (a, a, b):
            RefreshToken.for_user(u)
        out = StringIO()
        call_command('revoke_sessions', '--user', a.phone, '--dry-run', stdout=out)
        self.assertIn('2 refresh tokens would be revoked', out.getvalue())
        self.assertEqual(BlacklistedToken.objects.count(), 0)
        call_command('revoke_sessions', '--user', a.phone, stdout=StringIO())
        self.assertEqual(BlacklistedToken.objects.count(), 2)
        out = StringIO()
        call_command('revoke_sessions', '--all', stdout=out)
        self.assertIn('1 refresh tokens revoked', out.getvalue())
        with self.assertRaises(CommandError):
            call_command('revoke_sessions', '--user', '+920000000000', stdout=StringIO())
