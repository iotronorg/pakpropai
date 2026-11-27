"""T-2060 — availability, viewing slots, booking, reminders, feedback (ADR-037, FR-APT-001…006)."""
import json
import threading
from datetime import date, datetime, time, timedelta, timezone as dt_tz
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai import confirmation
from apps.ai import tool_registry as R
from apps.ai._tools_context import set_context
from apps.ai.models import PendingAction
from apps.core.models import OutboxMessage
from apps.enquiries.models import Enquiry
from apps.leads.models import Appointment, Lead
from apps.leads.tasks import send_appointment_reminders
from apps.notifications.models import Notification, UserNotificationPreference
from apps.organizations.models import OrganizationMembership
from apps.properties.models import Property
from apps.viewings import services as V
from apps.viewings.models import AvailabilityException, AvailabilityRule, ViewingFeedback
from tests.factories import make_agent, make_client, make_developer, make_lead, make_org, make_property
from tests.test_runtime import LOCMEM

PKT = ZoneInfo('Asia/Karachi')
A = Appointment.Status
API = '/api/v1/'
APPT = '/api/v1/leads/appointments/'


class World:
    def build(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime', operational_mode='production'))
        self.ua, self.agent = make_agent(org=self.org, name='Agent A', is_active=True)
        self.buyer = make_client(name='Buyer')
        self.p = make_property(org=self.org, title='Corner House')
        Property.objects.filter(pk=self.p.pk).update(is_sandbox=False)
        self.p.refresh_from_db()
        self.day = (timezone.now().astimezone(PKT) + timedelta(days=3)).date()
        self.rule(self.agent)

    def rule(self, agent, start=time(10), end=time(13), weekday=None, tz='Asia/Karachi', **kw):
        return AvailabilityRule.objects.create(agent=agent, weekday=self.day.weekday() if weekday is None else weekday,
                                               start=start, end=end, timezone=tz, **kw)

    def at(self, hour, minute=0):
        return datetime.combine(self.day, time(hour, minute), tzinfo=PKT).astimezone(dt_tz.utc)

    def api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user)
        return c

    def slot(self, hour, p=None):
        return f'{(p or self.p).pk}@{self.at(hour):%Y%m%dT%H%MZ}'

    def book(self, hour=10, user=None, p=None, **kw):
        return self.api(user or self.buyer).post(API + 'viewings/', {
            'slot_id': self.slot(hour, p), 'contact_consent': True, **kw}, format='json')

    def booked(self, **kw):
        r = self.book(**kw)
        assert r.status_code == 201, r.data
        return Appointment.objects.get(pk=r.data['id'])

    def hours(self, user=None, p=None):
        start = datetime.combine(self.day, time(0), tzinfo=PKT)
        return [s.start.astimezone(PKT).hour for s in V.slots(p or self.p, start, start + timedelta(days=1), user)]


@override_settings(CACHES=LOCMEM, EMAIL_HOST='')
class Base(World, TestCase):
    def setUp(self):
        self.build()


class AvailabilityTest(Base):
    def put(self, body, user=None):
        return self.api(user or self.ua).put(API + 'availability/', body, format='json')

    def test_replace_and_read(self):
        r = self.put({'rules': [{'weekday': 0, 'start': '09:00', 'end': '12:00'},
                                {'weekday': 0, 'start': '12:00', 'end': '13:00', 'slot_minutes': 30}],
                      'exceptions': [{'date': str(self.day), 'available': False}]})
        self.assertEqual((r.status_code, len(r.data['rules']), r.data['rules'][0]['timezone']), (200, 2, 'Asia/Karachi'))
        self.assertEqual(self.api(self.ua).get(API + 'availability/').data, r.data)
        self.assertEqual(AvailabilityRule.objects.count(), 2)

    def test_validation(self):
        ok = {'weekday': 0, 'start': '09:00', 'end': '12:00'}
        for rule in ({**ok, 'weekday': 7}, {**ok, 'end': '09:00'}, {**ok, 'timezone': 'Mars/X'}, {**ok, 'slot_minutes': 5}):
            self.assertEqual(self.put({'rules': [rule]}).status_code, 400, rule)
        self.assertEqual(self.put({'rules': [ok, {**ok, 'start': '11:00', 'end': '14:00'}]}).status_code, 400)
        for x in ({'available': True}, {'available': False, 'start': '09:00'}):
            self.assertEqual(self.put({'rules': [ok], 'exceptions': [{'date': str(self.day), **x}]}).status_code, 400)
        self.assertEqual(AvailabilityRule.objects.count(), 1)

    def test_agents_only(self):
        self.assertEqual(self.put({'rules': []}, self.buyer).status_code, 403)
        self.assertEqual(self.api().get(API + 'availability/').status_code, 401)


class SlotTest(Base):
    def test_rules(self):
        self.assertEqual(self.hours(), [10, 11, 12])
        AvailabilityRule.objects.update(slot_minutes=90)
        self.assertEqual(self.hours(), [10, 11])

    def test_exceptions(self):
        x = AvailabilityException.objects.create(agent=self.agent, date=self.day, available=False, start=time(10, 30),
                                                 end=time(11), timezone='Asia/Karachi')
        self.assertEqual(self.hours(), [11, 12])
        AvailabilityException.objects.create(agent=self.agent, date=self.day, available=True, start=time(18),
                                             end=time(19), timezone='Asia/Karachi')
        self.assertEqual(self.hours(), [11, 12, 18])
        x.start = x.end = None
        x.save()
        self.assertEqual(self.hours(), [18])

    def test_bookings_block_and_cancelled_ones_do_not(self):
        appt = Appointment.objects.create(lead=make_lead(make_client(), org=self.org), agent=self.agent,
                                          scheduled_at=self.at(10, 30), duration_minutes=60)
        self.assertEqual(self.hours(), [12])
        for s in (A.CANCELLED, A.COMPLETED, A.NO_SHOW):
            Appointment.objects.filter(pk=appt.pk).update(status=s)
            self.assertEqual(self.hours(), [10, 11, 12])

    def test_notice_and_horizon(self):
        with patch('apps.viewings.services.timezone.now', return_value=self.at(9)):
            self.assertEqual(self.hours(), [11, 12])
        with patch('apps.viewings.services.timezone.now', return_value=self.at(9) - timedelta(days=61)):
            self.assertEqual(self.hours(), [])

    def test_who_may_show_it(self):
        ub, b = make_agent(org=self.org, is_active=True)
        self.rule(b, time(15), time(16))
        self.assertEqual(self.hours(), [10, 11, 12, 15])
        Property.objects.filter(pk=self.p.pk).update(listed_by_agent=b)
        self.p.refresh_from_db()
        self.assertEqual(self.hours(), [15])
        make_lead(self.buyer, org=self.org, assigned_agent=self.agent)
        self.assertEqual((self.hours(self.buyer), self.hours(make_client())), ([10, 11, 12], [15]))
        OrganizationMembership.objects.filter(user=ub).update(is_active=False)
        self.assertEqual(self.hours(make_client()), [10, 11, 12])
        other = make_agent(org=make_org(name='Rival'), is_active=True)[1]
        self.rule(other, time(8), time(9))
        self.assertNotIn(8, self.hours())

    def test_private_and_freelance_listings(self):
        self.assertEqual(self.hours(p=make_property(owner=make_client())), [])
        _, f = make_agent(is_active=True)
        self.rule(f, time(15), time(16))
        self.assertEqual(self.hours(p=make_property(listing_owner_type='freelance_agent', listed_by_agent=f)), [15])

    def test_clock_change(self):
        """Rules are wall-clock time; a time that does not exist gives no slot."""
        from apps.viewings.services import _to_utc
        ny = ZoneInfo('America/New_York')
        self.assertIsNone(_to_utc(date(2027, 3, 14), time(2, 30), ny))
        self.assertEqual(_to_utc(date(2027, 3, 13), time(10), ny).hour, 15)
        self.assertEqual(_to_utc(date(2027, 3, 14), time(10), ny).hour, 14)

    def test_endpoint(self):
        get = lambda **q: self.api(self.buyer).get(API + 'viewing-slots/', {'listing': str(self.p.pk), **q})
        r = get(**{'from': str(self.day), 'days': '1'})
        self.assertEqual((r.data['timezone'], [s['local_start'][-5:] for s in r.data['slots']]),
                         ('Asia/Karachi', ['10:00', '11:00', '12:00']))
        self.assertEqual(r.data['slots'][0]['id'], self.slot(10))
        for q in ({'days': '15'}, {'days': 'x'}, {'from': 'x'}, {'listing': 'x'}, {'from': '9999-12-31'}):
            self.assertEqual(get(**q).status_code, 400, q)
        Property.objects.filter(pk=self.p.pk).update(is_sandbox=True)
        self.assertEqual(get().status_code, 404)
        self.assertEqual(self.api().get(API + 'viewing-slots/').status_code, 401)


class BookTest(Base):
    def test_books_through_a_consented_enquiry(self):
        a = self.booked(note='Parking?', participants=['Spouse'])
        e = Enquiry.objects.get()
        self.assertEqual((a.lead, a.enquiry, a.property, a.agent, a.scheduled_at, a.duration_minutes, a.status, a.timezone),
                         (e.lead, e, self.p, self.agent, self.at(10), 60, 'scheduled', 'Asia/Karachi'))
        self.assertEqual((a.lead.assigned_agent, a.lead.routing_state, a.participants, a.created_by),
                         (self.agent, 'agent_assigned', ['Spouse'], self.buyer))
        self.assertIn('10:00', e.messages.get().body)
        self.assertTrue(Notification.objects.filter(user=self.ua, title='New viewing booked').exists())

    def test_slot_ids_are_never_masked_as_card_or_account_numbers(self):     # KI-125
        from apps.compliance import middleware as M
        seen = 'd67df734-8254-4676-bd2c-db199df3acce@20261130T0500Z'      # was masked to "…df3[ACCOUNT]T0500Z"
        self.assertEqual(M._mask_strings_recursive({'slot_id': seen}, M._pipeline)[0], {'slot_id': seen})
        masked, found = M._mask_strings_recursive({'slot_id': seen + ' 4111111111111111'}, M._pipeline)
        self.assertNotEqual(masked['slot_id'], seen + ' 4111111111111111')         # only the exact format
        self.assertTrue(found)
        # Through the API, with a masker that flags anything containing a digit.
        real = M._pipeline.mask

        def greedy(text):
            r = real(text)
            if any(ch.isdigit() for ch in text):
                r.masked_text, r.detections = '[MASKED]', [*r.detections, type('D', (), {
                    'pattern_name': 'test', 'masked_value': '[MASKED]'})()]
            return r
        with patch.object(M._pipeline, 'mask', side_effect=greedy), \
                patch.object(M, 'report_detections') as reported:            # no event thread on its own connection
            r = self.book(note='Gate 2, please')
        self.assertTrue(reported.called)                                    # the masker did run on this body
        self.assertEqual(r.status_code, 201, r.data)

    def test_refusals_change_nothing(self):
        self.assertEqual(self.book(contact_consent=False).status_code, 400)
        self.assertEqual(self.book(hour=9).status_code, 409)
        for bad in ('x', f'{self.p.pk}@x', f'@{self.at(10):%Y%m%dT%H%MZ}'):
            self.assertEqual(self.book(slot_id=bad).status_code, 400, bad)
        self.assertEqual(self.book(participants=['a'] * 6).status_code, 400)
        self.assertEqual(self.book(user=self.ua).status_code, 409)         # own organization
        self.assertEqual(self.api().post(API + 'viewings/', {}).status_code, 401)
        self.assertFalse(Appointment.objects.exists() or Enquiry.objects.exists() or Lead.objects.exists())

    def test_a_slot_is_sold_once(self):
        self.booked()
        r = self.book(user=make_client())
        self.assertEqual((r.status_code, Appointment.objects.count()), (409, 1))

    def test_visitor_limits(self):
        self.booked()
        self.assertEqual(self.book(hour=11).status_code, 409)              # one per listing
        _, f = make_agent(is_active=True)
        self.rule(f, time(10), time(13))
        q = make_property(listing_owner_type='freelance_agent', listed_by_agent=f)
        Property.objects.filter(pk=q.pk).update(is_sandbox=False)
        self.assertEqual(self.book(hour=10, p=q).status_code, 409)         # already busy then
        with patch.object(V, 'MAX_UPCOMING', 1):
            self.assertEqual(self.book(hour=12, p=q).status_code, 429)
        self.assertEqual(self.book(hour=12, p=q).status_code, 201)
        self.assertEqual(Appointment.objects.filter(lead__user=self.buyer).count(), 2)

    def test_idempotency_key(self):
        c, body = self.api(self.buyer), {'slot_id': self.slot(10), 'contact_consent': True}
        got = [c.post(API + 'viewings/', body, format='json', HTTP_IDEMPOTENCY_KEY='k').status_code for _ in range(2)]
        self.assertEqual((got, Appointment.objects.count()), ([201, 201], 1))


class VisitorTest(Base):
    def call(self, a, action, user=None, **body):
        return self.api(user or self.buyer).post(f'{API}viewings/{a.pk}/{action}/', body, format='json')

    def test_only_own_viewings_and_no_internal_notes(self):
        a = self.booked()
        Appointment.objects.update(notes='INTERNAL', location='Gate 2')
        r = self.api(self.buyer).get(f'{API}viewings/{a.pk}/')
        self.assertEqual((r.data['location'], r.data['agent_name'], r.data['local_start'][-5:]),
                         ('Gate 2', self.agent.display_name, '10:00'))
        self.assertNotIn('INTERNAL', json.dumps(r.data, default=str))
        self.assertNotIn(self.agent.phone, json.dumps(r.data, default=str))
        for user in (make_client(), self.owner, self.ua):
            self.assertEqual(self.api(user).get(f'{API}viewings/{a.pk}/').status_code, 404)
            self.assertEqual(self.api(user).get(API + 'viewings/').data['results'], [])
            for action in ('cancel', 'reschedule', 'feedback'):
                self.assertEqual(self.call(a, action, user, rating=1, slot_id=self.slot(11)).status_code, 404)

    def test_cancel_frees_the_slot(self):
        a = self.booked()
        self.assertEqual(self.call(a, 'cancel').data['status'], 'cancelled')
        self.assertEqual(self.hours(), [10, 11, 12])
        self.assertEqual(self.call(a, 'cancel').status_code, 409)
        self.assertTrue(Notification.objects.filter(user=self.ua, title='Viewing cancelled').exists())

    def test_reschedule(self):
        a = self.booked()
        Appointment.objects.update(reminder_sent_at=timezone.now(), day_reminder_sent_at=timezone.now())
        self.assertEqual(self.call(a, 'reschedule', slot_id=self.slot(12)).status_code, 200)
        a.refresh_from_db()
        self.assertEqual((a.scheduled_at, a.status, a.reminder_sent_at, a.day_reminder_sent_at),
                         (self.at(12), 'rescheduled', None, None))
        self.assertEqual(self.hours(), [10, 11])
        self.assertEqual(self.call(a, 'reschedule', slot_id=self.slot(15)).status_code, 409)
        other = make_property(org=self.org)
        self.assertEqual(self.call(a, 'reschedule', slot_id=self.slot(11, other)).status_code, 400)
        Appointment.objects.update(scheduled_at=timezone.now() - timedelta(hours=3))
        self.assertEqual(self.call(a, 'reschedule', slot_id=self.slot(11)).status_code, 409)
        self.assertEqual(self.call(a, 'cancel').status_code, 409)

    def test_feedback(self):
        a = self.booked()
        self.assertEqual(self.call(a, 'feedback', rating=5).status_code, 409)      # not yet
        Appointment.objects.update(scheduled_at=timezone.now() - timedelta(hours=3))
        for bad in (0, 6, 'x'):
            self.assertEqual(self.call(a, 'feedback', rating=bad).status_code, 400)
        self.call(a, 'feedback', rating=2)
        r = self.call(a, 'feedback', rating=4, interested=True, notes='Nice')
        self.assertEqual((r.data['rating'], ViewingFeedback.objects.count()), (4, 1))
        agent = self.api(self.ua)
        self.assertEqual(agent.post(f'{APPT}{a.pk}/feedback/', {'rating': 3}, format='json').data['side'], 'agent')
        self.assertEqual([f['side'] for f in agent.get(f'{APPT}{a.pk}/feedback/').data], ['agent', 'user'])
        self.assertEqual(self.api(make_agent()[0]).get(f'{APPT}{a.pk}/feedback/').status_code, 404)
        Appointment.objects.update(status='cancelled')
        self.assertEqual(self.call(a, 'feedback', rating=1).status_code, 409)


class DashboardTest(Base):
    def setUp(self):
        super().setUp()
        self.lead = make_lead(make_client(), org=self.org, assigned_agent=self.agent)

    def create(self, start, user=None, **kw):
        return self.api(user or self.ua).post(APPT, {'lead': str(self.lead.pk), 'agent': self.agent.pk,
                                                     'scheduled_at': start.isoformat(), **kw}, format='json')

    def test_no_double_booking(self):
        self.assertEqual(self.create(self.at(10)).status_code, 201)
        self.assertEqual(self.create(self.at(10, 30)).status_code, 409)
        self.assertEqual(self.create(self.at(11)).status_code, 201)
        a, b = Appointment.objects.order_by('scheduled_at')
        move = lambda t: self.api(self.ua).post(f'{APPT}{b.pk}/reschedule/', {'scheduled_at': t}, format='json')
        self.assertEqual(move(self.at(10, 15).isoformat()).status_code, 409)
        self.assertEqual(self.api(self.ua).patch(f'{APPT}{b.pk}/', {'scheduled_at': self.at(10, 15).isoformat()},
                                                 format='json').status_code, 409)
        self.assertEqual(move((timezone.now() - timedelta(hours=1)).isoformat()).status_code, 400)
        self.assertEqual(move(f'{self.day}T16:00').status_code, 200)             # local time
        b.refresh_from_db()
        self.assertEqual(b.scheduled_at, self.at(16))

    def test_patch_moves_reset_reminders(self):
        self.create(self.at(10))
        Appointment.objects.update(reminder_sent_at=timezone.now())
        a = Appointment.objects.get()
        self.api(self.ua).patch(f'{APPT}{a.pk}/', {'scheduled_at': self.at(12).isoformat()}, format='json')
        a.refresh_from_db()
        self.assertEqual((a.scheduled_at, a.reminder_sent_at), (self.at(12), None))

    def test_rescheduled_visits_can_be_confirmed_completed_or_missed(self):
        self.create(self.at(10))
        a = Appointment.objects.get()
        act = lambda name: self.api(self.ua).post(f'{APPT}{a.pk}/{name}/').status_code
        self.assertEqual(act('no-show'), 400)
        for name in ('confirm', 'complete', 'no-show'):
            Appointment.objects.update(status='rescheduled', scheduled_at=timezone.now() - timedelta(hours=1))
            self.assertEqual(act(name), 200, name)

    def test_agent_without_organization_cannot_use_foreign_leads(self):
        """Was: any lead id was accepted, and the answer carried that lead's phone number."""
        fu, f = make_agent(is_active=True)
        self.assertEqual(self.create(self.at(10), fu).status_code, 400)
        self.assertEqual(self.create(self.at(10), fu, agent=f.pk).status_code, 400)
        mine = make_lead(make_client(), assigned_agent=f)
        ok = {'lead': str(mine.pk), 'agent': f.pk, 'scheduled_at': self.at(10).isoformat()}
        self.assertEqual(self.api(fu).post(APPT, {**ok, 'property': str(self.p.pk)}, format='json').status_code, 400)
        self.assertEqual(self.api(fu).post(APPT, ok, format='json').status_code, 201)

    def test_moved_agent_does_not_take_visits_to_the_new_organization(self):
        self.create(self.at(10))
        new_owner, new_org = make_developer(org=make_org(name='New'))
        type(self.agent).objects.filter(pk=self.agent.pk).update(organization=new_org)
        ids = lambda u: len(self.api(u).get(APPT).data['results'])
        self.assertEqual((ids(self.owner), ids(new_owner)), (1, 0))

    def test_input_limits(self):
        for kw in ({'duration_minutes': 481}, {'duration_minutes': 0}, {'participants': [{'x': 1}]},
                   {'meeting_url': 'javascript:alert(1)'}):
            self.assertEqual(self.create(self.at(10), **kw).status_code, 400, kw)


class ReminderTest(Base):
    def appt(self, **delta):
        a = self.booked()
        Appointment.objects.filter(pk=a.pk).update(scheduled_at=timezone.now() + timedelta(**delta))
        OutboxMessage.objects.all().delete()
        Notification.objects.all().delete()
        a.refresh_from_db()
        return a

    def texts(self):
        return [json.loads(m.payload)['text'] for m in OutboxMessage.objects.filter(kind='whatsapp.text')]

    def test_day_and_hour_before_once_each(self):
        a = self.appt(hours=23)
        self.assertEqual([send_appointment_reminders() for _ in range(2)], [1, 0])
        Appointment.objects.update(scheduled_at=timezone.now() + timedelta(minutes=50))
        self.assertEqual([send_appointment_reminders() for _ in range(2)], [1, 0])
        self.assertEqual(len(self.texts()), 2)
        self.assertEqual({n.user for n in Notification.objects.all()}, {self.buyer, self.ua})
        a.refresh_from_db()
        self.assertIsNotNone(a.day_reminder_sent_at and a.reminder_sent_at)

    def test_rescheduled_visits_are_reminded(self):
        self.appt(minutes=30)
        Appointment.objects.update(status='rescheduled', meeting_url='https://meet.example/x')
        self.assertEqual(send_appointment_reminders(), 1)
        self.assertIn('https://meet.example/x', self.texts()[0])

    def test_preferences(self):
        self.appt(minutes=30)
        UserNotificationPreference.objects.update_or_create(user=self.buyer, defaults={'whatsapp_enabled': False})
        send_appointment_reminders()
        self.assertEqual(self.texts(), [])

    def test_cancelled_is_not_reminded(self):
        self.appt(minutes=30)
        Appointment.objects.update(status='cancelled')
        self.assertEqual(send_appointment_reminders(), 0)

    def test_feedback_request_once(self):
        a = self.appt(hours=-5)
        Appointment.objects.update(status='confirmed')
        self.assertEqual([V.request_feedback() for _ in range(2)], [1, 0])
        Appointment.objects.update(feedback_requested_at=None)
        ViewingFeedback.objects.create(appointment=a, side='user', rating=4)
        self.assertEqual(V.request_feedback(), 0)


class PrivacyTest(Base):
    def test_erasure(self):
        a = self.booked(participants=['Spouse'])
        Appointment.objects.update(notes='private')
        ViewingFeedback.objects.create(appointment=a, side='user', rating=4, author=self.buyer)
        V.erase_user(self.buyer)
        a.refresh_from_db()
        self.assertEqual((a.status, a.notes, a.participants, ViewingFeedback.objects.count()),
                         ('cancelled', '[REDACTED]', [], 0))


class ToolTest(Base):
    def ctx(self, user=None):
        set_context(user or self.buyer, self.buyer.phone)

    def propose(self, start=None, **kw):
        self.ctx()
        return confirmation.propose(R.REGISTRY['book_viewing'], {
            'listing': self.p.ref_no, 'start': start or f'{self.day}T10:00', **kw})

    def test_find_slots(self):
        from apps.ai.tools import find_viewing_slots
        self.ctx()
        r = find_viewing_slots(self.p.ref_no, str(self.day), 1)
        self.assertEqual([s['start'] for s in r['slots']], [f'{self.day}T{h}:00' for h in (10, 11, 12)])
        self.assertEqual(find_viewing_slots('nope')['slots'], [])
        set_context(None, '1')
        self.assertIn('error', find_viewing_slots(self.p.ref_no))

    def test_books_only_after_yes_and_in_local_time(self):
        r = self.propose(note='Parking?')
        for shown in ('Corner House', '10:00 (Asia/Karachi)', 'Parking?', 'will be shared'):
            self.assertIn(shown, r['message'])
        self.assertFalse(Appointment.objects.exists())
        reply = confirmation.resolve_reply(self.buyer.phone, 'yes', user=self.buyer)
        a = Appointment.objects.get()
        self.assertIn('booked', reply)
        self.assertEqual((a.scheduled_at, a.enquiry.consent_via), (self.at(10), 'assistant'))
        self.assertEqual(PendingAction.objects.get().result['appointment_id'], str(a.pk))

    def test_invented_times_are_refused(self):
        for start in (f'{self.day}T09:00', f'{self.day}T10:07', 'tomorrow', '0001-01-01T00:00'):
            self.assertIn('error', self.propose(start), start)
        self.assertFalse(PendingAction.objects.exists())

    def test_slot_taken_before_the_yes(self):
        self.propose()
        self.booked(user=make_client())
        self.assertIn('not available', confirmation.resolve_reply(self.buyer.phone, 'yes', user=self.buyer))
        self.assertEqual(Appointment.objects.count(), 1)


class ChangeToolTest(Base):
    """KI-076: the visitor lists, cancels and moves their own viewings in the chat."""

    def setUp(self):
        super().setUp()
        self.a = self.booked()

    def ctx(self):
        set_context(self.buyer, self.buyer.phone)

    def ask(self, tool, user=None, org=None, **args):
        set_context(user or self.buyer, (user or self.buyer).phone, org=org)
        return confirmation.propose(R.REGISTRY[tool], args)

    def yes(self, user=None):
        return confirmation.resolve_reply((user or self.buyer).phone, 'yes', user=user or self.buyer)

    def test_lists_only_own_upcoming_viewings(self):
        from apps.ai.tools import my_viewings
        past = self.booked(hour=11, p=make_property(org=self.org, title='Old Flat'))
        Appointment.objects.filter(pk=past.pk).update(scheduled_at=timezone.now() - timedelta(hours=3))
        self.booked(hour=12, user=make_client(), p=make_property(org=self.org))
        self.ctx()
        r = my_viewings()
        self.assertEqual([(v['position'], v['viewing_id'], v['start'][-5:]) for v in r['viewings']],
                         [(1, str(self.a.pk), '10:00')])
        self.assertIn('Corner House', r['viewings'][0]['title'])
        self.assertNotIn(self.agent.phone, json.dumps(r))
        V.cancel(self.a)
        self.assertEqual(my_viewings()['viewings'], [])
        set_context(None, '1')
        self.assertIn('error', my_viewings())

    def test_cancel_only_after_yes(self):
        r = self.ask('cancel_viewing', viewing='1')
        for shown in ('Corner House', '10:00 (Asia/Karachi)', 'cancel'):
            self.assertIn(shown, r['message'])
        self.assertEqual(json.loads(PendingAction.objects.get().args), {'viewing': str(self.a.pk)})
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'scheduled')
        self.assertIn('cancelled', self.yes())
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'cancelled')
        self.assertTrue(Notification.objects.filter(user=self.ua, title='Viewing cancelled').exists())
        self.assertEqual(self.hours(), [10, 11, 12])

    def test_reschedule_only_after_yes(self):
        Appointment.objects.update(reminder_sent_at=timezone.now(), day_reminder_sent_at=timezone.now())
        r = self.ask('reschedule_viewing', viewing=self.p.ref_no, start=f'{self.day}T12:00')
        self.assertIn('New time: ' + V.local_text(self.at(12), PKT), r['message'])
        self.a.refresh_from_db()
        self.assertEqual(self.a.scheduled_at, self.at(10))
        self.assertIn('moved', self.yes())
        self.a.refresh_from_db()
        self.assertEqual((self.a.scheduled_at, self.a.status, self.a.reminder_sent_at, self.a.day_reminder_sent_at),
                         (self.at(12), 'rescheduled', None, None))
        self.assertEqual(self.hours(), [10, 11])
        self.assertTrue(Notification.objects.filter(user=self.ua, title='Viewing rescheduled').exists())

    def test_refusals_change_nothing(self):
        stranger = make_client()
        other_owner, other_org = make_developer(org=make_org(name='Other', operational_mode='production'))
        for user, org, tool, args in (
                (self.buyer, None, 'reschedule_viewing', {'viewing': '1', 'start': f'{self.day}T09:00'}),
                (self.buyer, None, 'reschedule_viewing', {'viewing': '1', 'start': f'{self.day}T10:00'}),
                (self.buyer, None, 'reschedule_viewing', {'viewing': '1', 'start': 'tomorrow'}),
                (self.buyer, None, 'cancel_viewing', {'viewing': '2'}),
                (self.buyer, None, 'cancel_viewing', {'viewing': 'RT-NOPE'}),
                (stranger, None, 'cancel_viewing', {'viewing': str(self.a.pk)}),
                (self.buyer, other_org, 'cancel_viewing', {'viewing': str(self.a.pk)})):   # another org's chat
            self.assertIn('error', self.ask(tool, user=user, org=org, **args), (tool, args))
        self.assertFalse(PendingAction.objects.exists())
        self.assertIsNotNone(self.ask('cancel_viewing', org=self.org, viewing='1').get('pending_action_id'))

    def test_time_taken_or_viewing_closed_before_the_yes(self):
        self.ask('reschedule_viewing', viewing='1', start=f'{self.day}T12:00')
        self.booked(hour=12, user=make_client(), p=make_property(org=self.org))
        self.assertIn('not available', self.yes())
        self.a.refresh_from_db()
        self.assertEqual((self.a.scheduled_at, self.a.status), (self.at(10), 'scheduled'))
        self.ask('cancel_viewing', viewing='1')
        V.cancel(self.a)
        self.assertIn('no longer', self.yes())

    def test_reminder_says_a_reply_works_only_while_the_tools_are_on(self):
        Appointment.objects.update(scheduled_at=timezone.now() + timedelta(minutes=30))
        send_appointment_reminders()
        self.assertIn(V.CHANGE_TEXT, json.loads(OutboxMessage.objects.filter(kind='whatsapp.text').last().payload)['text'])
        OutboxMessage.objects.all().delete()
        Appointment.objects.update(reminder_sent_at=None)
        with patch('apps.config.services.SystemConfigService.get_features', return_value={'feature_talk_to_agent': False}):
            send_appointment_reminders()
        text = json.loads(OutboxMessage.objects.get(kind='whatsapp.text').payload)['text']
        self.assertIn('in RealTron', text)
        self.assertNotIn('reply here', text)


@override_settings(CACHES=LOCMEM, EMAIL_HOST='')
class RaceTest(World, TransactionTestCase):
    def test_two_people_one_slot(self):
        self.build()
        people, out = [make_client(), make_client(), make_client()], []
        gate = threading.Barrier(len(people))

        def go(user):
            try:
                gate.wait()
                V.book(user, Property.objects.select_related('organization').get(pk=self.p.pk), self.at(10))
                out.append('ok')
            except V.ViewingError as exc:
                out.append(exc.code)
            finally:
                connection.close()
        threads = [threading.Thread(target=go, args=(u,)) for u in people]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(sorted(out), ['ok', 'slot_taken', 'slot_taken'])
        self.assertEqual((Appointment.objects.count(), Enquiry.objects.count()), (1, 1))
