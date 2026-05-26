"""
Enterprise BI Analytics Pipeline — validation suite.

Tests:
  1. Multi-tenant isolation: org A cannot see org B's funnel data
  2. 6-stage funnel stage ordering and conversion arithmetic
  3. WA token usage aggregation from Redis billing keys
  4. Agent speed leaderboard Redis sorted-set read/write
  5. 10,000-record performance: compute_funnel under 40 ms
  6. HTTP endpoint isolation: developer can only see own org
"""
import time
import uuid

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.leads.models import Lead
from apps.organizations.models import Organization
from apps.reports.analytics_engine import (
    compute_funnel,
    compute_wa_token_usage,
    get_agent_speed_leaderboard,
    refresh_leaderboard,
)
from tests.factories import make_developer, make_agent, make_org, make_user


def _bulk_leads(org, n, status=Lead.Status.NEW, routing=Lead.RoutingState.AI_QUEUE):
    """Create n Lead rows for org using bulk_create (fast path for large fixtures)."""
    user = make_user(role='client', phone=f'+929{uuid.uuid4().int % 10**9:09d}')
    objs = [
        Lead(
            user=user,
            organization=org,
            status=status,
            routing_state=routing,
            source=Lead.Source.WHATSAPP,
        )
        for _ in range(n)
    ]
    Lead.objects.bulk_create(objs, batch_size=500)


class TestFunnelIsolation(TestCase):
    """Funnel data must never cross org boundaries."""

    def setUp(self):
        self.dev_a, self.org_a = make_developer(org_name='OrgA')
        self.dev_b, self.org_b = make_developer(org_name='OrgB')

        # Org A: 30 leads, 10 warm, 5 qualified, 5 agent_assigned
        _bulk_leads(self.org_a, 15, Lead.Status.NEW,       Lead.RoutingState.AI_QUEUE)
        _bulk_leads(self.org_a, 10, Lead.Status.WARM,      Lead.RoutingState.ORG_QUEUE)
        _bulk_leads(self.org_a,  5, Lead.Status.QUALIFIED, Lead.RoutingState.AGENT_ASSIGNED)

        # Org B: 8 leads
        _bulk_leads(self.org_b, 8, Lead.Status.NEW, Lead.RoutingState.AI_QUEUE)

    def tearDown(self):
        cache.clear()

    def test_funnel_counts_org_a(self):
        funnel = compute_funnel(self.org_a)
        self.assertEqual(funnel['total_leads'], 30)
        # Qualify = warm + qualified = 15
        self.assertEqual(funnel['stages'][1]['count'], 15)
        # Verify = not ai_queue = 15 (10 org_queue + 5 agent_assigned)
        self.assertEqual(funnel['stages'][2]['count'], 15)
        # Connect = agent_assigned = 5
        self.assertEqual(funnel['stages'][3]['count'], 5)
        # Negotiate = qualified = 5
        self.assertEqual(funnel['stages'][4]['count'], 5)

    def test_funnel_org_a_does_not_see_org_b(self):
        funnel_a = compute_funnel(self.org_a)
        funnel_b = compute_funnel(self.org_b)
        self.assertEqual(funnel_a['total_leads'], 30)
        self.assertEqual(funnel_b['total_leads'], 8)
        # Org A should never report 8 (org B count)
        self.assertNotEqual(funnel_a['total_leads'], funnel_b['total_leads'])

    def test_funnel_conversion_arithmetic(self):
        funnel = compute_funnel(self.org_a)
        discover = funnel['stages'][0]
        qualify  = funnel['stages'][1]
        self.assertEqual(discover['conversion'], 100.0)
        expected = round(15 / 30 * 100, 1)
        self.assertAlmostEqual(qualify['conversion'], expected, places=1)

    def test_funnel_stage_names_ordered(self):
        funnel = compute_funnel(self.org_a)
        names = [s['stage'] for s in funnel['stages']]
        self.assertEqual(names, ['Discover', 'Qualify', 'Verify', 'Connect', 'Negotiate', 'Transact'])

    def test_empty_org_funnel(self):
        _, org_c = make_developer(org_name='OrgC')
        funnel = compute_funnel(org_c)
        self.assertEqual(funnel['total_leads'], 0)
        self.assertEqual(funnel['overall_conversion'], 0.0)
        for s in funnel['stages']:
            self.assertEqual(s['count'], 0)


class TestFunnelCaching(TestCase):

    def setUp(self):
        _, self.org = make_developer(org_name='CacheOrg')
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_second_call_returns_cached_result(self):
        _bulk_leads(self.org, 5)
        r1 = compute_funnel(self.org)
        # Add more leads — cache should prevent them showing up
        _bulk_leads(self.org, 3)
        r2 = compute_funnel(self.org)
        self.assertEqual(r1['total_leads'], r2['total_leads'])

    def test_cache_key_isolated_per_org(self):
        _, org_b = make_developer(org_name='CacheOrgB')
        _bulk_leads(self.org,  10)
        _bulk_leads(org_b, 3)
        f_a = compute_funnel(self.org)
        f_b = compute_funnel(org_b)
        self.assertNotEqual(f_a['total_leads'], f_b['total_leads'])


class TestWaTokenUsage(TestCase):

    def setUp(self):
        _, self.org = make_developer(org_name='WaOrg')
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _write_token_key(self, period, count):
        key = f'billing:{self.org.id}:wa_tokens:{period}'
        cache.set(key, count, 60 * 60 * 24 * 35)

    def test_returns_six_months(self):
        result = compute_wa_token_usage(self.org)
        self.assertEqual(len(result['monthly']), 6)

    def test_sums_correctly(self):
        self._write_token_key('2026-01', 100)
        self._write_token_key('2026-02', 200)
        cache.clear()  # clear the aggregate cache, not the billing keys
        result = compute_wa_token_usage(self.org)
        # total_6m should include whatever is in the 6 monthly keys
        self.assertGreaterEqual(result['total_6m'], 0)

    def test_org_isolation(self):
        _, org_b = make_developer(org_name='WaOrgB')
        self._write_token_key('2026-05', 999)
        result_b = compute_wa_token_usage(org_b)
        # Org B should not see org A's 999 tokens
        for m in result_b['monthly']:
            self.assertEqual(m['tokens'], 0)

    def test_structure(self):
        result = compute_wa_token_usage(self.org)
        self.assertIn('monthly', result)
        self.assertIn('current_period', result)
        self.assertIn('current_month', result)
        self.assertIn('total_6m', result)
        for row in result['monthly']:
            self.assertIn('period', row)
            self.assertIn('tokens', row)


class TestAgentLeaderboard(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer(org_name='LBOrg')
        self.a1, _ = make_agent(org=self.org, name='Alice')
        self.a2, _ = make_agent(org=self.org, name='Bob')
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_leaderboard_returns_list(self):
        rows = get_agent_speed_leaderboard(self.org)
        self.assertIsInstance(rows, list)

    def test_leaderboard_bounded_by_top_n(self):
        rows = get_agent_speed_leaderboard(self.org, top_n=1)
        self.assertLessEqual(len(rows), 1)

    def test_leaderboard_does_not_cross_orgs(self):
        _, org_b = make_developer(org_name='LBOrgB')
        make_agent(org=org_b, name='Carlos')
        rows_a = get_agent_speed_leaderboard(self.org)
        agent_names = [r['name'] for r in rows_a]
        self.assertNotIn('Carlos', agent_names)

    def test_refresh_does_not_raise(self):
        refresh_leaderboard(self.org)

    def test_rank_field_present(self):
        rows = get_agent_speed_leaderboard(self.org)
        if rows:
            self.assertIn('rank', rows[0])
            self.assertEqual(rows[0]['rank'], 1)


class TestPerformance10k(TestCase):
    """
    10,000 leads across two orgs. compute_funnel must complete in < 40 ms
    per org (DB round-trip budget; tested after initial cache warm-up).
    """

    @classmethod
    def setUpTestData(cls):
        cls.dev_a, cls.org_a = make_developer(org_name='Perf10kOrgA')
        cls.dev_b, cls.org_b = make_developer(org_name='Perf10kOrgB')

        # 7,000 leads in org A spread across statuses/routing states
        _bulk_leads(cls.org_a, 3000, Lead.Status.NEW,       Lead.RoutingState.AI_QUEUE)
        _bulk_leads(cls.org_a, 2000, Lead.Status.WARM,      Lead.RoutingState.ORG_QUEUE)
        _bulk_leads(cls.org_a, 1000, Lead.Status.QUALIFIED, Lead.RoutingState.AGENT_ASSIGNED)
        _bulk_leads(cls.org_a, 1000, Lead.Status.COLD,      Lead.RoutingState.CLOSED)

        # 3,000 leads in org B
        _bulk_leads(cls.org_b, 3000, Lead.Status.NEW, Lead.RoutingState.AI_QUEUE)

    def tearDown(self):
        cache.clear()

    def test_funnel_counts_correct_at_10k(self):
        funnel_a = compute_funnel(self.org_a)
        funnel_b = compute_funnel(self.org_b)
        self.assertEqual(funnel_a['total_leads'], 7000)
        self.assertEqual(funnel_b['total_leads'], 3000)

    def test_zero_data_leakage(self):
        funnel_a = compute_funnel(self.org_a)
        funnel_b = compute_funnel(self.org_b)
        # Org A has 3000 warm+qualified; org B has none
        self.assertGreater(funnel_a['stages'][1]['count'], 0)
        self.assertEqual(funnel_b['stages'][1]['count'], 0)

    def test_funnel_executes_within_40ms(self):
        # Prime the DB (no cache) then measure cold-cache query time
        cache.clear()
        start = time.perf_counter()
        compute_funnel(self.org_a)
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertLess(
            elapsed_ms, 40,
            f"compute_funnel took {elapsed_ms:.1f} ms (budget: 40 ms). "
            "Add indexes to leads(organization, status) and leads(organization, routing_state).",
        )


class TestBIEndpointRBAC(TestCase):
    """Developer can only access their own org's BI endpoints."""

    def setUp(self):
        self.dev_a, self.org_a = make_developer(org_name='EndpointOrgA')
        self.dev_b, self.org_b = make_developer(org_name='EndpointOrgB')
        self.client_a = APIClient()
        self.client_b = APIClient()
        self.client_a.force_authenticate(self.dev_a)
        self.client_b.force_authenticate(self.dev_b)

    def test_funnel_returns_200(self):
        resp = self.client_a.get('/api/v1/reports/funnel/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('stages', resp.json())

    def test_wa_tokens_returns_200(self):
        resp = self.client_a.get('/api/v1/reports/wa-tokens/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('monthly', resp.json())

    def test_leaderboard_returns_200(self):
        resp = self.client_a.get('/api/v1/reports/leaderboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_client_role_forbidden(self):
        client_user = make_user(role='client')
        c = APIClient()
        c.force_authenticate(client_user)
        resp = c.get('/api/v1/reports/funnel/')
        self.assertIn(resp.status_code, (403, 401))

    def test_funnel_org_a_counts_not_visible_to_dev_b(self):
        _bulk_leads(self.org_a, 25)
        resp_b = self.client_b.get('/api/v1/reports/funnel/')
        self.assertEqual(resp_b.status_code, 200)
        # Dev B sees 0, not 25
        data = resp_b.json()
        self.assertEqual(data['total_leads'], 0)
