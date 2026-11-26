"""T-3090 — AI ops console APIs: prompts, models, prices, tools, evals, traces (GAP-103, ADR-061)."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai.evals.offline import SuiteResult
from apps.ai.models import AIEvalRun, AIInteraction, AIKillSwitch, AITrace, ModelPrice
from apps.core.models import AuditEvent
from tests.factories import make_developer, make_org, make_user
from tests.test_runtime import LOCMEM

BASE = '/api/v1/ai/ops/'


def admin(platform_role):
    return make_user(role='admin', platform_role=platform_role)


def api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def trace(**kw):
    base = dict(route='llm', provider='gemini', model='gemini-2.5-flash', llm_calls=1, tokens_in=100, tokens_out=20,
                latency_ms=900, prompt_name='agent.system', prompt_version=10)
    base.update(kw)
    return AITrace.objects.create(**base)


@override_settings(CACHES=LOCMEM)
class AccessTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_only_ai_and_ops_admins(self):
        owner, _ = make_developer(org=make_org(name='Prime'))
        for user in (make_user(role='client'), owner, admin(''), admin('billing_admin')):
            with self.subTest(user=user.platform_role or user.role):
                self.assertEqual(api(user).get(BASE + 'overview/').status_code, 403)
        self.assertEqual(APIClient().get(BASE + 'overview/').status_code, 401)
        for role in ('ai_admin', 'ops_admin', 'super_admin'):
            self.assertEqual(api(admin(role)).get(BASE + 'overview/').status_code, 200)

    def test_ops_admins_read_but_do_not_change(self):
        ops = api(admin('ops_admin'))
        self.assertEqual(ops.post(BASE + 'evals/', {}, format='json').status_code, 403)
        self.assertEqual(ops.post(BASE + 'model-prices/', {'model': 'm', 'effective_from': '2026-11-01',
                                                           'input_per_mtok': 1, 'output_per_mtok': 2},
                                  format='json').status_code, 403)
        self.assertFalse(ModelPrice.objects.exists())


@override_settings(CACHES=LOCMEM)
class ReadTest(TestCase):
    def setUp(self):
        cache.clear()
        self.c = api(admin('ai_admin'))

    def test_overview(self):
        org = make_org(name='Prime')
        trace(organization=org, latency_ms=100, cost='0.002', currency='USD', pricing='priced')
        trace(latency_ms=300, fallback_used=True, provider='openai', model='gpt-x')
        trace(route='direct', llm_calls=0, latency_ms=5, provider='', model='', prompt_name='')
        trace(error='timeout', latency_ms=5000, pricing='unpriced')
        old = trace()
        AITrace.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=30))
        AIKillSwitch.objects.create(scope='organization', organization=org, reason='abuse')
        r = self.c.get(BASE + 'overview/')
        d = r.data
        self.assertEqual((d['turns'], d['model_turns'], d['fallback_turns'], d['failed_turns'], d['unpriced_turns']),
                         (4, 3, 1, 1, 1))
        self.assertEqual((d['by_route'], d['by_model']['gemini-2.5-flash']), ({'llm': 3, 'direct': 1}, 2))
        self.assertEqual(d['latency_ms'], {'p50': 300, 'p95': 5000, 'max': 5000})
        self.assertEqual(d['errors'], {'': 3, 'timeout': 1})
        self.assertEqual(str(d['cost'][0]['total']), '0.00200000')
        self.assertEqual([k['reason'] for k in d['active_kill_switches']], ['abuse'])
        self.assertEqual(self.c.get(BASE + 'overview/', {'from': '2026-01-01', 'to': '2026-12-31'}).status_code, 400)
        self.assertEqual(self.c.get(BASE + 'overview/', {'from': 'monday'}).status_code, 400)

    def test_prompts(self):
        AIInteraction.objects.create(interaction_type='listing_assist', prompt_name='listing.describe',
                                     prompt_version=1, prompt_digest='old000000000')
        r = self.c.get(BASE + 'prompts/')
        names = {p['name']: p for p in r.data['results']}
        self.assertTrue({'agent.system', 'faq.answer', 'listing.describe', 'listing.extract'} <= set(names))
        self.assertEqual(names['listing.describe']['calls'], 1)
        self.assertNotIn('template', names['listing.describe'])
        d = self.c.get(BASE + 'prompts/listing.describe/').data
        self.assertIn('{market_name}', d['template'])
        self.assertEqual(d['versions_in_use'][0] | {'first_used': None, 'last_used': None},
                         {'version': 1, 'digest': 'old000000000', 'current': False, 'calls': 1,
                          'first_used': None, 'last_used': None})
        self.assertEqual(self.c.get(BASE + 'prompts/nothing.here/').status_code, 404)

    def test_models_with_prices_and_usage(self):
        ModelPrice.objects.create(model='gemini-2.5-flash', effective_from='2026-01-01', input_per_mtok='0.3',
                                  output_per_mtok='2.5')
        trace()
        trace(error='timeout')

        class Fake:
            def __init__(self, name):
                self.name = name

            def is_configured(self):
                return self.name == 'gemini'

            @property
            def label(self):
                return f'{self.name}:gemini-2.5-flash' if self.name == 'gemini' else f'{self.name}:other'

        with patch('apps.ai.llm.provider_order', return_value=['gemini', 'openai']), \
                patch('apps.ai.backends.make_backend', Fake):
            rows = self.c.get(BASE + 'models/').data['results']
        g, o = rows
        self.assertEqual((g['provider'], g['order'], g['configured'], g['model'], g['breaker_open']),
                         ('gemini', 1, True, 'gemini-2.5-flash', False))
        self.assertEqual((str(g['price']['input_per_mtok']), g['usage']['turns'], g['usage']['errors']),
                         ('0.300000', 2, 1))
        self.assertEqual((o['configured'], o['price'], o['usage']['turns']), (False, None, 0))

    def test_tools(self):
        trace(tool_calls=[{'name': 'search_listings', 'status': 'ok', 'ms': 30},
                          {'name': 'search_listings', 'status': 'error', 'ms': 5}])
        rows = {t['name']: t for t in self.c.get(BASE + 'tools/').data['results']}
        s = rows['search_listings']
        self.assertEqual(s['calls'], {'ok': 1, 'error': 1})
        self.assertTrue(s['enabled'])
        self.assertTrue(any(t['requires_confirmation'] for t in rows.values()))

    def test_traces_hold_no_person(self):
        org = make_org(name='Prime')
        user = make_user(role='client')
        t = trace(organization=org, user=user, conversation_ref='abc')
        trace(route='cache', fallback_used=True)
        r = self.c.get(BASE + 'traces/')
        self.assertEqual(r.data['count'], 2)
        self.assertEqual(self.c.get(BASE + 'traces/', {'organization': str(org.pk)}).data['count'], 1)
        self.assertEqual(self.c.get(BASE + 'traces/', {'route': 'cache'}).data['count'], 1)
        self.assertEqual(self.c.get(BASE + 'traces/', {'fallback': 'true'}).data['count'], 1)
        self.assertEqual(self.c.get(BASE + 'traces/', {'organization': 'x'}).status_code, 400)
        one = self.c.get(f'{BASE}traces/{t.pk}/').data
        self.assertEqual((one['conversation_ref'], one['organization_id']), ('abc', str(org.pk)))
        self.assertNotIn('user', one)
        self.assertNotIn('user_id', one)
        self.assertNotIn(user.phone, str(r.data))
        self.assertEqual(self.c.get(f'{BASE}traces/00000000-0000-0000-0000-000000000000/').status_code, 404)


@override_settings(CACHES=LOCMEM)
class WriteTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = admin('ai_admin')
        self.c = api(self.user)

    def test_add_price(self):
        body = {'model': 'gemini-2.5-flash', 'effective_from': '2026-12-01', 'input_per_mtok': '0.30',
                'output_per_mtok': '2.50'}
        r = self.c.post(BASE + 'model-prices/', body, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.c.post(BASE + 'model-prices/', body, format='json').status_code, 409)
        bad = self.c.post(BASE + 'model-prices/', {'model': '', 'effective_from': 'soon', 'currency': 'dollars',
                                                   'input_per_mtok': '-1', 'output_per_mtok': 'x'}, format='json')
        self.assertEqual(set(bad.json()['error_info']['fields']), {'model', 'effective_from', 'currency', 'input_per_mtok', 'output_per_mtok'})
        self.assertEqual(len(self.c.get(BASE + 'model-prices/', {'model': 'gemini-2.5-flash'}).data['results']), 1)
        e = AuditEvent.objects.get(action='ai.model_price_added')
        self.assertEqual((e.category, e.actor_id, e.meta['input_per_mtok']), ('ai', str(self.user.pk), '0.30'))

    def test_eval_run_is_stored(self):
        r = self.c.post(BASE + 'evals/', {'suites': ['safety', 'listing_copy']}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data['passed'])
        self.assertEqual([s['suite'] for s in r.data['suites']], ['safety', 'listing_copy'])
        self.assertIn('by_language', r.data['suites'][0])
        run = AIEvalRun.objects.get()
        self.assertEqual((run.started_by, run.failed), (str(self.user.pk), 0))
        listed = self.c.get(BASE + 'evals/').data
        self.assertEqual((listed['count'], listed['results'][0]['id']), (1, str(run.id)))
        self.assertNotIn('failures', listed['results'][0]['suites'][0])
        self.assertEqual(self.c.get(f'{BASE}evals/{run.id}/').data['total'], run.total)
        self.assertTrue(AuditEvent.objects.filter(action='ai.eval_run', target_id=str(run.id)).exists())

    def test_failed_run_keeps_some_failures(self):
        bad = SuiteResult('safety')
        for i in range(30):
            bad.add({'id': f'c{i}', 'lang': 'en'}, False, 'refuse', 'answer')
        with patch('apps.ai.evals.offline.run', return_value=[bad]):
            r = self.c.post(BASE + 'evals/', {}, format='json')
        self.assertEqual((r.data['passed'], r.data['failed'], len(r.data['suites'][0]['failures'])), (False, 30, 20))

    def test_eval_refusals(self):
        self.assertEqual(self.c.post(BASE + 'evals/', {'suites': ['vibes']}, format='json').status_code, 400)
        self.assertEqual(self.c.post(BASE + 'evals/', {'suites': 'safety'}, format='json').status_code, 400)
        self.assertEqual(self.c.post(BASE + 'evals/', {'live': True}, format='json').status_code, 400)
        self.assertFalse(AIEvalRun.objects.exists())
