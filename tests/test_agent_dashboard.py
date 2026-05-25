from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from apps.agents.models import Agent
from apps.leads.models import LeadActivity
from apps.agents.stats import _compute_response_time
from tests.factories import make_developer, make_agent, make_lead, make_client


class ResponseTimeCalculationTests(TestCase):
    def setUp(self):
        _, self.org = make_developer()
        _, self.agent = make_agent(org=self.org)
        self.lead = make_lead(make_client(), org=self.org, assigned_agent=self.agent)

    def test_correct_gap_hours(self):
        """ASSIGNED at t=0, CONTACTED at t=2 -> 2.0h."""
        base = timezone.now() - timedelta(hours=10)
        a1 = LeadActivity.objects.create(lead=self.lead, action='assigned')
        a2 = LeadActivity.objects.create(lead=self.lead, action='contacted')
        LeadActivity.objects.filter(pk=a1.pk).update(created_at=base)
        LeadActivity.objects.filter(pk=a2.pk).update(created_at=base + timedelta(hours=2))
        self.assertEqual(_compute_response_time([self.lead.id]), 2.0)

    def test_contacted_before_assigned_excluded(self):
        """CONTACTED before ASSIGNED -> no valid pair -> None."""
        base = timezone.now() - timedelta(hours=10)
        a1 = LeadActivity.objects.create(lead=self.lead, action='contacted')
        a2 = LeadActivity.objects.create(lead=self.lead, action='assigned')
        LeadActivity.objects.filter(pk=a1.pk).update(created_at=base)
        LeadActivity.objects.filter(pk=a2.pk).update(created_at=base + timedelta(hours=1))
        self.assertIsNone(_compute_response_time([self.lead.id]))

    def test_assigned_without_contacted_excluded(self):
        """ASSIGNED but no CONTACTED -> None (not 0h)."""
        LeadActivity.objects.create(lead=self.lead, action='assigned')
        self.assertIsNone(_compute_response_time([self.lead.id]))

    def test_multiple_leads_correct_average(self):
        """Two leads with 2h and 4h gaps -> avg 3.0h."""
        lead2 = make_lead(make_client(), org=self.org, assigned_agent=self.agent)
        base = timezone.now() - timedelta(hours=10)
        a1 = LeadActivity.objects.create(lead=self.lead, action='assigned')
        a2 = LeadActivity.objects.create(lead=self.lead, action='contacted')
        a3 = LeadActivity.objects.create(lead=lead2,      action='assigned')
        a4 = LeadActivity.objects.create(lead=lead2,      action='contacted')
        LeadActivity.objects.filter(pk=a1.pk).update(created_at=base)
        LeadActivity.objects.filter(pk=a2.pk).update(created_at=base + timedelta(hours=2))
        LeadActivity.objects.filter(pk=a3.pk).update(created_at=base)
        LeadActivity.objects.filter(pk=a4.pk).update(created_at=base + timedelta(hours=4))
        self.assertEqual(_compute_response_time([self.lead.id, lead2.id]), 3.0)

    def test_empty_lead_ids_returns_none(self):
        """Empty lead_ids list -> None."""
        self.assertIsNone(_compute_response_time([]))

    def test_first_assigned_activity_used(self):
        """Two ASSIGNED activities - only the first counts."""
        base = timezone.now() - timedelta(hours=10)
        a1 = LeadActivity.objects.create(lead=self.lead, action='assigned')
        a2 = LeadActivity.objects.create(lead=self.lead, action='assigned')
        a3 = LeadActivity.objects.create(lead=self.lead, action='contacted')
        LeadActivity.objects.filter(pk=a1.pk).update(created_at=base)
        LeadActivity.objects.filter(pk=a2.pk).update(created_at=base + timedelta(hours=1))
        LeadActivity.objects.filter(pk=a3.pk).update(created_at=base + timedelta(hours=3))
        # gap is from first assigned (base) to contacted (base+3h) = 3.0h
        self.assertEqual(_compute_response_time([self.lead.id]), 3.0)


class ComputeLeaderboardTests(TestCase):
    def setUp(self):
        _, self.org = make_developer()
        _, self.agent_a = make_agent(org=self.org)
        _, self.agent_b = make_agent(org=self.org)
        # agent_a: 1 closed out of 2 (50%)
        make_lead(make_client(), org=self.org, assigned_agent=self.agent_a, routing_state='closed')
        make_lead(make_client(), org=self.org, assigned_agent=self.agent_a)
        # agent_b: 0 leads (0%)

    def test_sorted_by_conversion_rate_desc(self):
        from apps.agents.models import Agent
        from apps.agents.stats import compute_leaderboard
        qs = Agent.objects.filter(organization=self.org)
        results = compute_leaderboard(qs)
        self.assertEqual(results[0]['agent_id'], self.agent_a.id)
        self.assertEqual(results[0]['conversion_rate'], 50.0)
        self.assertEqual(results[1]['agent_id'], self.agent_b.id)
        self.assertEqual(results[1]['conversion_rate'], 0.0)

    def test_rank_is_sequential_from_one(self):
        from apps.agents.models import Agent
        from apps.agents.stats import compute_leaderboard
        qs = Agent.objects.filter(organization=self.org)
        results = compute_leaderboard(qs)
        ranks = [r['rank'] for r in results]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_zero_leads_agent_has_zero_conversion(self):
        from apps.agents.models import Agent
        from apps.agents.stats import compute_leaderboard
        qs = Agent.objects.filter(id=self.agent_b.id)
        results = compute_leaderboard(qs)
        self.assertEqual(results[0]['conversion_rate'], 0.0)
        self.assertIsNone(results[0]['avg_response_time_hours'])


from django.test import override_settings
from rest_framework.test import APIClient

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=_LOCMEM)
class AgentStatsViewTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        _, self.org  = make_developer()
        self.dev_user = self.org.admin_user
        _, self.agent = make_agent(org=self.org)
        _, self.org2  = make_developer()
        self.dev_user2 = self.org2.admin_user
        _, self.agent2 = make_agent(org=self.org2)

    def test_agent_sees_own_stats(self):
        self.api.force_authenticate(user=self.agent.user)
        r = self.api.get(f'/api/v1/agents/{self.agent.id}/stats/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('conversion_rate', r.data)
        self.assertIn('avg_response_time_hours', r.data)

    def test_agent_denied_other_agents_stats(self):
        self.api.force_authenticate(user=self.agent.user)
        r = self.api.get(f'/api/v1/agents/{self.agent2.id}/stats/')
        self.assertEqual(r.status_code, 403)

    def test_developer_sees_own_org_agent(self):
        self.api.force_authenticate(user=self.dev_user)
        r = self.api.get(f'/api/v1/agents/{self.agent.id}/stats/')
        self.assertEqual(r.status_code, 200)

    def test_developer_denied_other_org_agent(self):
        self.api.force_authenticate(user=self.dev_user)
        r = self.api.get(f'/api/v1/agents/{self.agent2.id}/stats/')
        self.assertEqual(r.status_code, 403)

    def test_admin_sees_any_agent(self):
        admin = make_client()
        admin.role = 'admin'; admin.save()
        self.api.force_authenticate(user=admin)
        r = self.api.get(f'/api/v1/agents/{self.agent2.id}/stats/')
        self.assertEqual(r.status_code, 200)

    def test_conversion_rate_calculated(self):
        make_lead(make_client(), org=self.org, assigned_agent=self.agent, routing_state='closed')
        make_lead(make_client(), org=self.org, assigned_agent=self.agent)
        self.api.force_authenticate(user=self.dev_user)
        r = self.api.get(f'/api/v1/agents/{self.agent.id}/stats/')
        self.assertEqual(r.data['conversion_rate'], 50.0)

    def test_response_time_null_with_no_activities(self):
        self.api.force_authenticate(user=self.dev_user)
        r = self.api.get(f'/api/v1/agents/{self.agent.id}/stats/')
        self.assertIsNone(r.data['avg_response_time_hours'])


@override_settings(CACHES=_LOCMEM)
class LeaderboardViewTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        _, self.org  = make_developer()
        self.dev_user = self.org.admin_user
        _, self.agent_a = make_agent(org=self.org)
        _, self.agent_b = make_agent(org=self.org)
        _, self.org2    = make_developer()
        self.dev_user2  = self.org2.admin_user
        _, self.agent_c = make_agent(org=self.org2)

        # Approve and activate all test agents so they appear in leaderboard
        Agent.objects.filter(
            id__in=[self.agent_a.id, self.agent_b.id, self.agent_c.id]
        ).update(is_active=True, registration_status=Agent.RegistrationStatus.APPROVED)
        self.agent_a.refresh_from_db()
        self.agent_b.refresh_from_db()
        self.agent_c.refresh_from_db()

        # Give agent_a 1 closed lead out of 2 (50% conversion)
        make_lead(make_client(), org=self.org, assigned_agent=self.agent_a, routing_state='closed')
        make_lead(make_client(), org=self.org, assigned_agent=self.agent_a)

    def test_developer_gets_org_scoped_results(self):
        self.api.force_authenticate(user=self.dev_user)
        r = self.api.get('/api/v1/agents/leaderboard/')
        self.assertEqual(r.status_code, 200)
        ids = [e['agent_id'] for e in r.data['results']]
        self.assertIn(self.agent_a.id, ids)
        self.assertIn(self.agent_b.id, ids)
        self.assertNotIn(self.agent_c.id, ids)
        self.assertEqual(r.data['count'], len(r.data['results']))

    def test_agent_role_denied(self):
        self.api.force_authenticate(user=self.agent_a.user)
        r = self.api.get('/api/v1/agents/leaderboard/')
        self.assertEqual(r.status_code, 403)

    def test_cross_org_isolation(self):
        self.api.force_authenticate(user=self.dev_user2)
        r = self.api.get('/api/v1/agents/leaderboard/')
        self.assertEqual(r.status_code, 200)
        ids = [e['agent_id'] for e in r.data['results']]
        self.assertNotIn(self.agent_a.id, ids)
        self.assertIn(self.agent_c.id, ids)
        self.assertEqual(r.data['count'], len(r.data['results']))

    def test_rank_is_sequential_from_one(self):
        self.api.force_authenticate(user=self.dev_user)
        r = self.api.get('/api/v1/agents/leaderboard/')
        ranks = [e['rank'] for e in r.data['results']]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_admin_sees_all_orgs(self):
        admin = make_client()
        admin.role = 'admin'
        admin.save()
        self.api.force_authenticate(user=admin)
        r = self.api.get('/api/v1/agents/leaderboard/')
        self.assertEqual(r.status_code, 200)
        ids = [e['agent_id'] for e in r.data['results']]
        self.assertIn(self.agent_a.id, ids)
        self.assertIn(self.agent_c.id, ids)


@override_settings(CACHES=_LOCMEM)
class MyStatsResponseTimeTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        _, self.agent = make_agent()  # freelance (no org needed for /reports/my-stats/)

    def test_response_time_field_present(self):
        self.api.force_authenticate(user=self.agent.user)
        r = self.api.get('/api/v1/reports/my-stats/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('avg_response_time_hours', r.data)

    def test_response_time_null_when_no_activities(self):
        self.api.force_authenticate(user=self.agent.user)
        r = self.api.get('/api/v1/reports/my-stats/')
        self.assertIsNone(r.data['avg_response_time_hours'])

    def test_pre_existing_fields_still_present(self):
        self.api.force_authenticate(user=self.agent.user)
        r = self.api.get('/api/v1/reports/my-stats/')
        for field in ('total_leads', 'closed_leads', 'hot_leads', 'avg_score',
                      'by_status', 'by_source', 'total_listings', 'closed_deals',
                      'rating', 'is_verified'):
            self.assertIn(field, r.data, msg=f"Missing field: {field}")

    def test_closed_leads_uses_routing_state(self):
        """Regression: closed_leads must count routing_state=closed, not status='closed'."""
        from apps.leads.models import Lead
        make_lead(make_client(), assigned_agent=self.agent,
                  routing_state=Lead.RoutingState.CLOSED)
        self.api.force_authenticate(user=self.agent.user)
        r = self.api.get('/api/v1/reports/my-stats/')
        self.assertEqual(r.data['closed_leads'], 1)
