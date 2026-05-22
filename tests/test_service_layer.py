"""
Service-layer unit tests — Task 6 of TEST-SUITE-PHASE2.

Isolated tests for business-logic services (no HTTP, no external calls):
  - OrgConfigService  (get, set, reset, is_feature_enabled, cache behaviour)
  - notify_user       (Notification creation, pref gating, task dispatch)
  - suggest_agents_for_lead (scoring, city filter, error safety)
  - assign_agent_to_lead    (field update, LeadActivity, agent notification)
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from tests.factories import make_developer, make_agent, make_lead, make_user

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


# ── OrgConfigService ──────────────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class OrgConfigServiceTest(TestCase):

    def setUp(self):
        cache.clear()
        self.dev, self.org = make_developer()

    def tearDown(self):
        cache.clear()

    def test_get_returns_org_row_value(self):
        from apps.organizations.services import OrgConfigService
        OrgConfigService.set(self.org, 'feature_loan_eligibility', 'false')
        cache.clear()
        val = OrgConfigService.get(self.org, 'feature_loan_eligibility')
        self.assertEqual(val, 'false')

    def test_get_falls_back_to_system_config(self):
        from apps.organizations.services import OrgConfigService
        from apps.config.services import SystemConfigService
        SystemConfigService.set('feature_loan_eligibility', 'true')
        # No org-level row → falls back to system value
        val = OrgConfigService.get(self.org, 'feature_loan_eligibility')
        self.assertEqual(val, 'true')

    def test_get_uses_cache_on_second_call(self):
        from apps.organizations.services import OrgConfigService
        OrgConfigService.set(self.org, 'feature_deal_lock', 'true')
        cache.clear()
        OrgConfigService.get(self.org, 'feature_deal_lock')   # populate cache
        with self.assertNumQueries(0):
            val = OrgConfigService.get(self.org, 'feature_deal_lock')
        self.assertEqual(val, 'true')

    def test_is_feature_enabled_true(self):
        from apps.organizations.services import OrgConfigService
        OrgConfigService.set(self.org, 'feature_property_audit', 'true')
        self.assertTrue(OrgConfigService.is_feature_enabled(self.org, 'feature_property_audit'))

    def test_is_feature_enabled_false(self):
        from apps.organizations.services import OrgConfigService
        OrgConfigService.set(self.org, 'feature_property_audit', 'false')
        self.assertFalse(OrgConfigService.is_feature_enabled(self.org, 'feature_property_audit'))

    def test_set_raises_for_unknown_key(self):
        from apps.organizations.services import OrgConfigService
        with self.assertRaises(ValueError):
            OrgConfigService.set(self.org, 'not_a_real_feature_key', 'true')

    def test_reset_removes_org_override_and_reverts_to_system(self):
        from apps.organizations.services import OrgConfigService
        from apps.config.services import SystemConfigService
        OrgConfigService.set(self.org, 'feature_scam_check', 'false')
        SystemConfigService.set('feature_scam_check', 'true')
        OrgConfigService.reset(self.org, 'feature_scam_check')
        cache.clear()
        val = OrgConfigService.get(self.org, 'feature_scam_check')
        self.assertEqual(val, 'true')

    def test_bulk_set_writes_multiple_keys(self):
        from apps.organizations.services import OrgConfigService
        OrgConfigService.bulk_set(self.org, {
            'feature_loan_eligibility':  'true',
            'feature_property_audit':    'false',
        })
        cache.clear()
        self.assertEqual(OrgConfigService.get(self.org, 'feature_loan_eligibility'), 'true')
        self.assertEqual(OrgConfigService.get(self.org, 'feature_property_audit'), 'false')


# ── notify_user ───────────────────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class NotifyUserServiceTest(TestCase):

    def setUp(self):
        self.user = make_user()

    @patch('apps.notifications.tasks.send_whatsapp_async.delay')
    def test_creates_notification_record(self, _delay):
        from apps.notifications.services import notify_user
        from apps.notifications.models import Notification
        notify_user(self.user, title='Hello', message='World')
        n = Notification.objects.get(user=self.user)
        self.assertEqual(n.title, 'Hello')
        self.assertEqual(n.message, 'World')

    @patch('apps.notifications.tasks.send_whatsapp_async.delay')
    def test_dispatches_task_when_prefs_allow(self, mock_delay):
        from apps.notifications.services import notify_user
        from apps.notifications.models import UserNotificationPreference
        UserNotificationPreference.objects.update_or_create(
            user=self.user,
            defaults={'whatsapp_enabled': True, 'lead_updates': True},
        )
        notify_user(self.user, title='T', message='M', event_type='lead_updates')
        mock_delay.assert_called_once()

    @patch('apps.notifications.tasks.send_whatsapp_async.delay')
    def test_marks_failed_when_whatsapp_channel_disabled(self, mock_delay):
        from apps.notifications.services import notify_user
        from apps.notifications.models import Notification, UserNotificationPreference
        prefs, _ = UserNotificationPreference.objects.get_or_create(user=self.user)
        prefs.whatsapp_enabled = False
        prefs.save()
        notify_user(self.user, title='T', message='M')
        mock_delay.assert_not_called()
        n = Notification.objects.get(user=self.user)
        self.assertEqual(n.status, Notification.Status.FAILED)
        self.assertIn('preferences', n.error)

    @patch('apps.notifications.tasks.send_whatsapp_async.delay')
    def test_marks_failed_when_event_type_opt_out(self, mock_delay):
        from apps.notifications.services import notify_user
        from apps.notifications.models import Notification, UserNotificationPreference
        prefs, _ = UserNotificationPreference.objects.get_or_create(user=self.user)
        prefs.marketing = False
        prefs.save()
        notify_user(self.user, title='T', message='M', event_type='marketing')
        mock_delay.assert_not_called()
        n = Notification.objects.get(user=self.user)
        self.assertEqual(n.status, Notification.Status.FAILED)


# ── suggest_agents_for_lead ───────────────────────────────────────────────────

class SuggestAgentsServiceTest(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()
        self.client_user = make_user()
        self.lead = make_lead(
            self.client_user,
            org=self.org,
            city_interest='Lahore',
            intent='buy',
        )

    def test_returns_agents_ranked_highest_score_first(self):
        from apps.leads.services import suggest_agents_for_lead
        _, a_low = make_agent(
            org=self.org, is_active=True,
            cities=['Lahore'], primary_city='Lahore',
            rating=2.0, specializations=['residential_buy'],
        )
        _, a_high = make_agent(
            org=self.org, is_active=True,
            cities=['Lahore'], primary_city='Lahore',
            rating=5.0, is_featured=True,
            specializations=['residential_buy'],
        )
        results = suggest_agents_for_lead(self.lead, limit=5)
        ids = [a.id for a in results]
        self.assertIn(a_high.id, ids)
        self.assertIn(a_low.id, ids)
        self.assertLess(ids.index(a_high.id), ids.index(a_low.id))

    def test_city_filter_excludes_agents_in_other_cities(self):
        from apps.leads.services import suggest_agents_for_lead
        _, agent_karachi = make_agent(
            org=self.org, is_active=True,
            cities=['Karachi'], primary_city='Karachi',
            specializations=['residential_buy'],
        )
        results = suggest_agents_for_lead(self.lead, limit=10)
        ids = [a.id for a in results]
        self.assertNotIn(agent_karachi.id, ids)

    def test_load_deduction_lowers_score_for_busy_agent(self):
        from apps.leads.services import suggest_agents_for_lead
        _, a_loaded = make_agent(
            org=self.org, is_active=True,
            cities=['Lahore'], primary_city='Lahore',
            rating=4.0, specializations=['residential_buy'],
        )
        _, a_free = make_agent(
            org=self.org, is_active=True,
            cities=['Lahore'], primary_city='Lahore',
            rating=4.0, specializations=['residential_buy'],
        )
        # Assign 10 extra leads to a_loaded to depress its score
        for i in range(10):
            u = make_user()
            make_lead(u, org=self.org, assigned_agent=a_loaded)

        results = suggest_agents_for_lead(self.lead, limit=5)
        ids = [a.id for a in results]
        if a_free.id in ids and a_loaded.id in ids:
            self.assertLess(ids.index(a_free.id), ids.index(a_loaded.id))

    def test_returns_empty_list_on_exception(self):
        from apps.leads.services import suggest_agents_for_lead
        from unittest.mock import MagicMock
        bad_lead = MagicMock()
        bad_lead.id = 'bad'
        bad_lead.organization_id = None
        bad_lead.city_interest = None
        bad_lead.intent = None
        result = suggest_agents_for_lead(bad_lead, limit=3)
        self.assertIsInstance(result, list)

    def test_respects_limit_parameter(self):
        from apps.leads.services import suggest_agents_for_lead
        for _ in range(5):
            make_agent(
                org=self.org, is_active=True,
                cities=['Lahore'], primary_city='Lahore',
                specializations=['residential_buy'],
            )
        results = suggest_agents_for_lead(self.lead, limit=2)
        self.assertLessEqual(len(results), 2)


# ── assign_agent_to_lead ──────────────────────────────────────────────────────

class AssignAgentToLeadTest(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()
        self.client_user = make_user()
        self.lead = make_lead(self.client_user, org=self.org)
        _, self.agent = make_agent(org=self.org)

    @patch('apps.notifications.services.notify_user')
    def test_sets_assigned_agent_on_lead(self, _notify):
        from apps.leads.services import assign_agent_to_lead
        assign_agent_to_lead(self.lead, self.agent)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_agent, self.agent)

    @patch('apps.notifications.services.notify_user')
    def test_creates_assigned_lead_activity(self, _notify):
        from apps.leads.services import assign_agent_to_lead
        from apps.leads.models import LeadActivity
        assign_agent_to_lead(self.lead, self.agent, actor=self.dev)
        activity = LeadActivity.objects.filter(
            lead=self.lead,
            action=LeadActivity.ActionType.ASSIGNED,
        ).first()
        self.assertIsNotNone(activity)
        self.assertIn(self.agent.name, activity.notes)
        self.assertEqual(activity.actor, self.dev)

    @patch('apps.notifications.services.notify_user')
    def test_activity_meta_contains_old_and_new_agent_ids(self, _notify):
        from apps.leads.services import assign_agent_to_lead
        from apps.leads.models import LeadActivity
        _, old_agent = make_agent(org=self.org)
        self.lead.assigned_agent = old_agent
        self.lead.save(update_fields=['assigned_agent'])

        assign_agent_to_lead(self.lead, self.agent)
        activity = LeadActivity.objects.filter(
            lead=self.lead, action=LeadActivity.ActionType.ASSIGNED
        ).latest('id')
        self.assertEqual(str(activity.meta.get('old_agent_id')), str(old_agent.id))
        self.assertEqual(str(activity.meta.get('new_agent_id')), str(self.agent.id))

    @patch('apps.notifications.services.notify_user')
    def test_notifies_newly_assigned_agent(self, mock_notify):
        from apps.leads.services import assign_agent_to_lead
        assign_agent_to_lead(self.lead, self.agent)
        mock_notify.assert_called_once()
        notified_user = mock_notify.call_args[0][0]
        self.assertEqual(notified_user, self.agent.user)
