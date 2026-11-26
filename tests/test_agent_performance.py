"""T-3010 — agent performance from real events (FR-CRM-008, ADR-056)."""
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.enquiries import services as E
from apps.leads.models import Appointment, ConversationMessage, Lead
from apps.organizations.models import OrganizationMembership
from apps.properties.models import Property
from apps.reports import performance as P
from apps.viewings.models import ViewingFeedback
from tests.factories import make_agent, make_client, make_developer, make_membership, make_org, make_property, make_user
from tests.test_runtime import LOCMEM

PKT = ZoneInfo('Asia/Karachi')
URL = '/api/v1/reports/agent-performance/'


@override_settings(CACHES=LOCMEM, EMAIL_HOST='')
class AgentPerformanceTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime', operational_mode='production'))
        self.ua, self.a = make_agent(org=self.org, name='Agent A', is_active=True)
        self.ub, self.b = make_agent(org=self.org, name='Agent B', is_active=True)
        self.p = make_property(org=self.org, title='Corner House')
        Property.objects.filter(pk=self.p.pk).update(is_sandbox=False)
        self.p.refresh_from_db()
        self.day = timezone.now().astimezone(PKT).date() - timedelta(days=5)

    def local(self, hour, minute=0, day=None):
        return datetime.combine(day or self.day, time(hour, minute), tzinfo=PKT)

    def enquiry(self, agent, at, reply_after=None):
        e, _ = E.create(make_client(), message='Is it available?', listing=self.p)
        Lead.objects.filter(pk=e.lead_id).update(assigned_agent=agent)
        type(e).objects.filter(pk=e.pk).update(created_at=at)
        if reply_after is not None:
            for gap in (reply_after, reply_after + timedelta(hours=5)):      # only the first reply counts
                m = ConversationMessage.objects.create(lead=e.lead, enquiry=e, body='Yes', channel='in_app',
                                                       direction=ConversationMessage.Direction.OUTBOUND)
                ConversationMessage.objects.filter(pk=m.pk).update(created_at=at + gap)
        return e

    def visit(self, agent, status, at=None, rating=None):
        lead = Lead.objects.create(user=make_client(), organization=self.org, assigned_agent=agent)
        appt = Appointment.objects.create(lead=lead, property=self.p, agent=agent, status=status,
                                          scheduled_at=at or self.local(15), timezone='Asia/Karachi')
        if rating:
            ViewingFeedback.objects.create(appointment=appt, side='user', rating=rating)
            ViewingFeedback.objects.create(appointment=appt, side='agent', rating=1)    # the agent's own: not counted
        return appt

    def get(self, user, **params):
        c = APIClient()
        c.force_authenticate(user)
        return c.get(URL, {'from': str(self.day), 'to': str(self.day), **params})

    def row(self, data, agent):
        return next(r for r in data['agents'] if r['agent_id'] == agent.pk)

    def test_enquiry_reply_times(self):
        self.enquiry(self.a, self.local(10), timedelta(minutes=30))
        self.enquiry(self.a, self.local(11), timedelta(hours=3))
        self.enquiry(self.a, self.local(12))
        self.enquiry(self.b, self.local(12), timedelta(minutes=10))
        r = self.get(self.owner)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.row(r.data, self.a)['enquiries'], {
            'received': 3, 'answered': 2, 'unanswered': 1, 'median_first_reply_minutes': 105.0,
            'answered_within_1h_pct': 33.3})
        self.assertEqual(r.data['totals']['enquiries']['received'], 4)
        self.assertEqual(r.data['totals']['enquiries']['median_first_reply_minutes'], 30.0)
        self.assertEqual((r.data['timezone'], r.data['from'], r.data['to']), ('Asia/Karachi', str(self.day), str(self.day)))

    def test_days_are_local(self):
        self.enquiry(self.a, self.local(0, 30))                                  # 19:30 UTC the day before
        self.enquiry(self.a, self.local(23, 59, self.day - timedelta(days=1)))   # previous local day
        self.assertEqual(self.row(self.get(self.owner).data, self.a)['enquiries']['received'], 1)

    def test_viewings_and_feedback(self):
        S = Appointment.Status
        self.visit(self.a, S.COMPLETED, rating=4)
        self.visit(self.a, S.COMPLETED, rating=5)
        self.visit(self.a, S.NO_SHOW)
        self.visit(self.a, S.CANCELLED)
        self.visit(self.a, S.CONFIRMED)
        self.visit(self.a, S.COMPLETED, at=self.local(15) + timedelta(days=2))   # outside the period
        other = make_org(name='Elsewhere')
        Appointment.objects.create(lead=Lead.objects.create(user=make_client(), organization=other), agent=self.a,
                                   scheduled_at=self.local(16), status=S.COMPLETED)   # another org's visit
        v = self.row(self.get(self.owner).data, self.a)['viewings']
        self.assertEqual(v, {'scheduled': 5, 'completed': 2, 'no_show': 1, 'cancelled': 1, 'no_show_pct': 33.3,
                             'feedback_count': 2, 'feedback_avg': 4.5})

    def test_lead_conversion(self):
        for status, state in (('qualified', 'agent_assigned'), ('new', 'closed'), ('new', 'agent_assigned'),
                              ('cold', 'agent_assigned')):
            lead = Lead.objects.create(user=make_client(), organization=self.org, assigned_agent=self.b,
                                       status=status, routing_state=state)
            Lead.objects.filter(pk=lead.pk).update(created_at=self.local(9))
        self.assertEqual(self.row(self.get(self.owner).data, self.b)['leads'],
                         {'assigned': 4, 'qualified': 1, 'closed': 1, 'conversion_pct': 25.0})

    def test_who_sees_what(self):
        self.enquiry(self.a, self.local(10), timedelta(minutes=5))
        everyone = {self.a.pk, self.b.pk}
        self.assertEqual({r['agent_id'] for r in self.get(self.owner).data['agents']}, everyone)
        self.assertEqual({r['agent_id'] for r in self.get(self.owner, agent=self.b.pk).data['agents']}, {self.b.pk})
        manager = make_user(role='developer')
        make_membership(manager, self.org, role=OrganizationMembership.Role.SALES_MANAGER)
        self.assertEqual({r['agent_id'] for r in self.get(manager).data['agents']}, everyone)
        mine = self.get(self.ub, agent=self.a.pk).data                        # an agent: own row only
        self.assertEqual([r['agent_id'] for r in mine['agents']], [self.b.pk])
        self.assertEqual(mine['totals']['enquiries']['received'], 0)
        viewer = make_user(role='developer')
        make_membership(viewer, self.org, role=OrganizationMembership.Role.VIEWER)
        for user in (viewer, make_client()):
            self.assertEqual(self.get(user).status_code, 403)
        _, foreign = make_agent(org=make_org(name='Other'), name='Agent X', is_active=True)
        self.assertNotIn(foreign.pk, {r['agent_id'] for r in self.get(self.owner).data['agents']})
        admin = make_user(role='admin')
        self.assertEqual(self.get(admin).status_code, 400)
        self.assertEqual({r['agent_id'] for r in self.get(admin, organization=self.org.pk).data['agents']}, everyone)
        body = json.dumps(self.get(self.owner).data)
        for phone in (self.ua.phone, self.ub.phone, self.a.phone):
            self.assertNotIn(phone.lstrip('+'), body)

    def test_period_validation(self):
        for params in ({'from': 'yesterday'}, {'from': '2026-02-10', 'to': '2026-02-01'},
                       {'from': '2025-01-01', 'to': '2026-06-01'}, {'agent': 'x'}):
            self.assertEqual(self.get(self.owner, **params).status_code, 400, params)
        today = date(2026, 3, 31)
        p = P.period(self.org, today=today)
        self.assertEqual(((p.last - p.first).days + 1, p.last), (30, today))
        self.assertEqual(P.period(self.org, '2026-01-01', '2026-12-31', today=today).first, date(2026, 1, 1))

    def test_empty(self):
        r = self.get(self.owner)
        self.assertEqual(self.row(r.data, self.a)['enquiries']['median_first_reply_minutes'], None)
        self.assertEqual(r.data['totals']['viewings']['feedback_avg'], None)
