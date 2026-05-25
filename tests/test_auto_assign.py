"""
Tests for auto_assign_unassigned_leads task.
Covers: feature flag gate (org-level + platform-level), age threshold,
        already-assigned skip, no-candidates skip, activity meta, double-assign guard.
"""
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.leads.models import Lead, LeadActivity
from tests.factories import make_user, make_developer, make_agent, make_lead

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_EAGER  = {'CELERY_TASK_ALWAYS_EAGER': True}


def _old_lead(org, minutes_ago=40, status=Lead.Status.NEW):
    user = make_user(role='client')
    lead = Lead.objects.create(user=user, organization=org, status=status)
    # auto_now_add=True ignores kwarg; force the timestamp via queryset update
    Lead.objects.filter(pk=lead.pk).update(
        created_at=timezone.now() - timedelta(minutes=minutes_ago)
    )
    lead.refresh_from_db()
    return lead


def _enable_auto_assign_for_org(org):
    from apps.organizations.models import OrganizationConfig
    OrganizationConfig.objects.update_or_create(
        organization=org, key='feature_auto_assign', defaults={'value': 'true'}
    )


@override_settings(CACHES=_LOCMEM, **_EAGER)
class AutoAssignFlagTests(TestCase):

    def setUp(self):
        _, self.org = make_developer()
        self.agent_user, self.agent = make_agent(org=self.org)
        self.lead = _old_lead(self.org, minutes_ago=40)

    def test_skips_when_flag_off(self):
        """No org config row = feature off = nothing assigned."""
        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()
        self.assertEqual(count, 0)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.assigned_agent)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_assigns_when_org_flag_on(self, mock_suggest):
        mock_suggest.return_value = [self.agent]
        _enable_auto_assign_for_org(self.org)

        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()

        self.assertEqual(count, 1)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_agent, self.agent)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_assigns_when_platform_flag_on(self, mock_suggest):
        mock_suggest.return_value = [self.agent]
        from apps.config.models import SystemConfig
        SystemConfig.objects.update_or_create(
            key='feature_auto_assign', defaults={'value': 'true'}
        )

        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()
        self.assertEqual(count, 1)


@override_settings(CACHES=_LOCMEM, **_EAGER)
class AutoAssignThresholdTests(TestCase):

    def setUp(self):
        _, self.org = make_developer()
        self.agent_user, self.agent = make_agent(org=self.org)
        _enable_auto_assign_for_org(self.org)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_skips_lead_younger_than_threshold(self, mock_suggest):
        mock_suggest.return_value = [self.agent]
        young_lead = _old_lead(self.org, minutes_ago=10)

        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()
        self.assertEqual(count, 0)
        young_lead.refresh_from_db()
        self.assertIsNone(young_lead.assigned_agent)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_assigns_lead_older_than_threshold(self, mock_suggest):
        mock_suggest.return_value = [self.agent]
        old_lead = _old_lead(self.org, minutes_ago=35)

        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()
        self.assertEqual(count, 1)
        old_lead.refresh_from_db()
        self.assertEqual(old_lead.assigned_agent, self.agent)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_skips_already_assigned_lead(self, mock_suggest):
        mock_suggest.return_value = [self.agent]
        assigned_lead = _old_lead(self.org, minutes_ago=40)
        assigned_lead.assigned_agent = self.agent
        assigned_lead.save()

        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()
        self.assertEqual(count, 0)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_skips_lead_with_no_candidates(self, mock_suggest):
        mock_suggest.return_value = []
        _old_lead(self.org, minutes_ago=40)

        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()
        self.assertEqual(count, 0)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_skips_cold_lead(self, mock_suggest):
        mock_suggest.return_value = [self.agent]
        _old_lead(self.org, minutes_ago=40, status=Lead.Status.COLD)

        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()
        self.assertEqual(count, 0)


@override_settings(CACHES=_LOCMEM, **_EAGER)
class AutoAssignActivityTests(TestCase):

    def setUp(self):
        _, self.org = make_developer()
        self.agent_user, self.agent = make_agent(org=self.org)
        _enable_auto_assign_for_org(self.org)
        self.lead = _old_lead(self.org, minutes_ago=40)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_creates_activity_with_auto_meta(self, mock_suggest):
        mock_suggest.return_value = [self.agent]

        from apps.leads.tasks import auto_assign_unassigned_leads
        auto_assign_unassigned_leads()

        activity = LeadActivity.objects.filter(
            lead=self.lead,
            action=LeadActivity.ActionType.ASSIGNED,
        ).first()
        self.assertIsNotNone(activity)
        self.assertTrue(activity.meta.get('auto'))

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_no_double_assign_on_second_run(self, mock_suggest):
        mock_suggest.return_value = [self.agent]

        from apps.leads.tasks import auto_assign_unassigned_leads
        auto_assign_unassigned_leads()
        count2 = auto_assign_unassigned_leads()

        self.assertEqual(count2, 0)  # already assigned, second run skips it


@override_settings(CACHES=_LOCMEM, **_EAGER)
class AutoAssignOrgIsolationTests(TestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.delete('sysconfig:feature_auto_assign')
        _, self.org1 = make_developer()
        _, self.org2 = make_developer()
        self.agent_user, self.agent1 = make_agent(org=self.org1)
        # Enable only for org1
        _enable_auto_assign_for_org(self.org1)

    @patch('apps.leads.services.suggest_agents_for_lead')
    def test_only_processes_opted_in_org(self, mock_suggest):
        mock_suggest.return_value = [self.agent1]
        lead_org1 = _old_lead(self.org1, minutes_ago=40)
        lead_org2 = _old_lead(self.org2, minutes_ago=40)

        from apps.leads.tasks import auto_assign_unassigned_leads
        count = auto_assign_unassigned_leads()

        self.assertEqual(count, 1)
        lead_org1.refresh_from_db()
        lead_org2.refresh_from_db()
        self.assertEqual(lead_org1.assigned_agent, self.agent1)
        self.assertIsNone(lead_org2.assigned_agent)
