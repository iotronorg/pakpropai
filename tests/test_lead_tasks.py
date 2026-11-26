"""T-3020 — tasks and follow-ups on leads with reminders (FR-CRM-004, ADR-057)."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.leads import follow_ups as F
from apps.leads.models import Lead, LeadActivity, LeadTask
from apps.leads.tasks import send_lead_task_reminders
from apps.notifications.models import Notification
from apps.organizations.models import OrganizationMembership
from tests.factories import make_agent, make_client, make_developer, make_membership, make_org, make_user
from tests.test_runtime import LOCMEM

URL = '/api/v1/leads/tasks/'


@override_settings(CACHES=LOCMEM, EMAIL_HOST='')
class LeadTaskTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.ua, self.a = make_agent(org=self.org, name='Agent A', is_active=True)
        self.ub, self.b = make_agent(org=self.org, name='Agent B', is_active=True)
        self.lead_a = Lead.objects.create(user=make_client(), organization=self.org, assigned_agent=self.a)
        self.lead_b = Lead.objects.create(user=make_client(), organization=self.org, assigned_agent=self.b)
        self.due = timezone.now() + timedelta(days=1)

    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def add(self, user, lead=None, **body):
        return self.api(user).post(URL, {'lead': str((lead or self.lead_a).pk), 'title': 'Call back',
                                         'due_at': self.due.isoformat(), **body}, format='json')

    def task(self, user=None, **body):
        r = self.add(user or self.owner, **body)
        assert r.status_code == 201, r.data
        return LeadTask.objects.get(pk=r.data['id'])

    def patch(self, user, t, **body):
        return self.api(user).patch(f'{URL}{t.pk}/', body, format='json')

    def test_create(self):
        r = self.add(self.owner, notes='Ask about parking', kind='meeting')
        self.assertEqual(r.status_code, 201, r.data)
        t = LeadTask.objects.get(pk=r.data['id'])
        self.assertEqual((t.assignee, t.organization, t.remind_at, t.created_by, r.data['assignee_name']),
                         (self.a, self.org, t.due_at, self.owner, 'Agent A'))
        self.assertTrue(LeadActivity.objects.filter(lead=self.lead_a, notes='Task added: Call back').exists())
        self.assertEqual(self.add(self.owner, assignee=self.b.pk).data['assignee'], self.b.pk)

    def test_agents_create_only_for_themselves_on_their_leads(self):
        self.assertEqual(self.add(self.ua).data['assignee'], self.a.pk)
        self.assertEqual(self.add(self.ua, lead=self.lead_b).status_code, 404)
        self.assertEqual(self.add(self.ua, assignee=self.b.pk).status_code, 400)

    def test_refusals(self):
        now = timezone.now()
        _, stranger = make_agent(org=make_org(name='Other'), is_active=True)
        orphan = Lead.objects.create(user=make_client())
        for body, code in (({'due_at': (now - timedelta(hours=1)).isoformat()}, 400),
                           ({'due_at': (now + timedelta(days=400)).isoformat()}, 400),
                           ({'remind_at': (self.due + timedelta(minutes=1)).isoformat()}, 400),
                           ({'assignee': stranger.pk}, 400), ({'title': '  '}, 400),
                           ({'orphan': True}, 400)):
            if body.pop('orphan', False):
                r = self.add(make_user(role='admin'), lead=orphan)
            else:
                r = self.add(self.owner, **body)
            self.assertEqual(r.status_code, code, body)
        other_owner, _ = make_developer(org=make_org(name='Third'))
        self.assertEqual(self.add(other_owner).status_code, 404)
        self.assertFalse(LeadTask.objects.exists())

    def test_who_sees_what(self):
        on_a = self.task()
        on_b = self.task(lead=self.lead_b)
        given_to_a = self.task(lead=self.lead_b, assignee=self.a.pk)
        ids = lambda user, **q: {r['id'] for r in self.api(user).get(URL, q).data['results']}   # noqa: E731
        self.assertEqual(ids(self.owner), {str(on_a.pk), str(on_b.pk), str(given_to_a.pk)})
        self.assertEqual(ids(self.ua), {str(on_a.pk), str(given_to_a.pk)})
        self.assertEqual(ids(self.ub), {str(on_b.pk), str(given_to_a.pk)})          # the lead is hers
        self.assertEqual(ids(self.ub, mine='true'), {str(on_b.pk)})
        crm = make_user(role='developer')
        make_membership(crm, self.org, role=OrganizationMembership.Role.CRM_OPERATOR)
        self.assertEqual(len(ids(crm)), 3)
        viewer = make_user(role='developer')
        make_membership(viewer, self.org, role=OrganizationMembership.Role.VIEWER)
        other_owner, _ = make_developer(org=make_org(name='Third'))
        for user in (viewer, make_client(), other_owner):
            self.assertEqual(ids(user), set())
            self.assertEqual(self.api(user).get(f'{URL}{on_a.pk}/').status_code, 404)
        self.assertEqual(self.patch(self.ub, on_a, status='done').status_code, 404)

    def test_done_and_reopen(self):
        t = self.task()
        r = self.patch(self.ua, t, status='done')
        self.assertEqual(r.status_code, 200, r.data)
        t.refresh_from_db()
        self.assertEqual(t.status, 'done')
        self.assertIsNotNone(t.completed_at)
        self.assertTrue(LeadActivity.objects.filter(lead=self.lead_a, notes='Task done: Call back').exists())
        self.assertEqual(self.patch(self.ua, t, due_at=(self.due + timedelta(days=1)).isoformat()).status_code, 400)
        self.assertEqual(self.patch(self.ua, t, status='open').data['completed_at'], None)
        self.assertEqual(str(self.patch(self.ua, t, lead=str(self.lead_b.pk)).data['lead']), str(self.lead_a.pk))  # read-only
        self.assertEqual(self.api(self.ua).put(f'{URL}{t.pk}/', {}, format='json').status_code, 405)

    def test_moving_keeps_the_reminder_lead_time_and_resets_it(self):
        t = self.task(remind_at=(self.due - timedelta(minutes=30)).isoformat())
        LeadTask.objects.filter(pk=t.pk).update(reminded_at=timezone.now())
        later = self.due + timedelta(days=2)
        self.patch(self.ua, t, due_at=later.isoformat())
        t.refresh_from_db()
        self.assertEqual((t.due_at, t.remind_at, t.reminded_at), (later, later - timedelta(minutes=30), None))

    def test_only_managers_reassign(self):
        t = self.task()
        self.assertEqual(self.patch(self.ua, t, assignee=self.b.pk).status_code, 400)
        LeadTask.objects.filter(pk=t.pk).update(reminded_at=timezone.now())
        self.assertEqual(self.patch(self.owner, t, assignee=self.b.pk).data['assignee'], self.b.pk)
        t.refresh_from_db()
        self.assertIsNone(t.reminded_at)

    def test_reminders_once_to_the_assignee(self):
        t = self.task(remind_at=(timezone.now() + timedelta(minutes=10)).isoformat())
        done = self.task()
        self.patch(self.owner, done, status='cancelled')
        self.assertEqual(send_lead_task_reminders(), 0)                              # not yet
        LeadTask.objects.update(remind_at=timezone.now() - timedelta(minutes=1))   # the cancelled one too
        self.assertEqual([send_lead_task_reminders() for _ in range(2)], [1, 0])
        n = Notification.objects.get(user=self.ua, title='Task due')
        self.assertIn('Call back', n.message)
        self.assertNotIn(self.lead_a.user.phone.lstrip('+'), n.message)
        t.refresh_from_db()
        self.assertIsNotNone(t.reminded_at)
        self.assertEqual(F.send_reminders(), 0)

    def test_due_filters(self):
        overdue = self.task()
        past = timezone.now() - timedelta(hours=1)
        LeadTask.objects.filter(pk=overdue.pk).update(due_at=past, remind_at=past)
        upcoming = self.task()
        ids = lambda **q: {r['id'] for r in self.api(self.owner).get(URL, q).data['results']}   # noqa: E731
        self.assertEqual(ids(due='overdue'), {str(overdue.pk)})
        self.assertEqual(ids(due='upcoming'), {str(upcoming.pk)})
        self.assertEqual(ids(status='done'), set())
        self.assertEqual(ids(lead='not-a-uuid'), set())
        self.assertTrue(self.api(self.owner).get(f'{URL}{overdue.pk}/').data['overdue'])
