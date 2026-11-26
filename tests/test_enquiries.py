"""T-2050 — consented enquiries, in-app threads, email (ADR-036, FR-COM-001/002/003)."""
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai import confirmation
from apps.ai import tool_registry as R
from apps.ai._tools_context import set_context
from apps.ai.models import PendingAction
from apps.core import outbox
from apps.core.models import OutboxMessage
from apps.enquiries import services as S
from apps.enquiries.models import Enquiry
from apps.leads.models import ConversationMessage, Lead
from apps.leads.utils import upsert_lead
from apps.notifications.models import Notification, UserNotificationPreference
from apps.organizations.models import OrganizationMembership
from apps.properties.models import Property
from tests.factories import make_agent, make_client, make_developer, make_membership, make_org, make_property, make_user
from tests.test_runtime import LOCMEM

ROLE = OrganizationMembership.Role
ENQ, THR = '/api/v1/enquiries/', '/api/v1/threads/'


@override_settings(CACHES=LOCMEM, EMAIL_HOST='smtp.test', FRONTEND_URL='https://app.test')
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime', operational_mode='production',
                                                           phone='+923001110000'))
        self.owner.email = 'boss@prime.example'
        self.owner.save()
        self.ua, self.agent = make_agent(org=self.org, name='Agent A', is_active=True)
        self.ub, self.agent_b = make_agent(org=self.org, name='Agent B', is_active=True)
        self.buyer = make_client(phone='+923215550001', name='Buyer Name', email='buyer@mail.example')
        self.other, self.other_org = make_developer(org=make_org(name='Rival', operational_mode='production'))
        self.p = self.live(make_property(org=self.org, title='Corner House'))

    def live(self, p, **kw):
        Property.objects.filter(pk=p.pk).update(is_sandbox=False, **kw)
        p.refresh_from_db()
        return p

    def api(self, user=None):
        c = APIClient()
        if user:
            c.force_authenticate(user)
        return c

    def enquire(self, user=None, listing=None, **kw):
        body = {'listing_id': str((listing or self.p).pk), 'message': 'Is it available?', 'contact_consent': True, **kw}
        return self.api(user or self.buyer).post(ENQ, {k: v for k, v in body.items() if v is not None}, format='json')

    def thread(self, **kw):
        return Enquiry.objects.get(pk=self.enquire(**kw).data['id'])

    def say(self, user, e, body='Yes'):
        return self.api(user).post(f'{THR}{e.pk}/messages/', {'body': body}, format='json')

    def read(self, user, e):
        return self.api(user).get(f'{THR}{e.pk}/messages/')


class CreateTest(Base):
    def test_creates_lead_thread_and_message(self):
        r = self.enquire(preferred_channel='email')
        self.assertEqual(r.status_code, 201)
        e = Enquiry.objects.get()
        self.assertEqual((e.user, e.organization, e.listing, e.kind, e.consent_version, e.consent_via),
                         (self.buyer, self.org, self.p, 'organization', S.CONSENT_VERSION, 'api'))
        lead = e.lead
        self.assertEqual((lead.organization, lead.source, lead.routing_state, lead.intent, lead.is_sandbox),
                         (self.org, 'web', 'org_queue', 'buy', False))
        m = ConversationMessage.objects.get()
        self.assertEqual((m.lead, m.enquiry, m.direction, m.channel, m.body),
                         (lead, e, 'inbound', 'in_app', 'Is it available?'))

    def test_consent_and_shape_are_required(self):
        for kw in ({'contact_consent': False}, {'contact_consent': None}, {'message': '  '}, {'message': 'x' * 2001},
                   {'listing_id': None}, {'preferred_channel': 'fax'},
                   {'requirement': {'intent': 'buy', 'city': 'Lahore'}}):
            self.assertEqual(self.enquire(**kw).status_code, 400, kw)
        self.assertEqual(self.api().post(ENQ, {}, format='json').status_code, 401)
        self.assertFalse(Enquiry.objects.exists() or Lead.objects.exists())

    def test_hidden_listings_are_404(self):
        for kw in ({'is_sandbox': True}, {'is_active': False}, {'expires_at': timezone.now() - timedelta(minutes=1)}):
            p = make_property(org=self.org)
            Property.objects.filter(pk=p.pk).update(**{'is_sandbox': False, **kw})
            self.assertEqual(self.enquire(listing=p).status_code, 404, kw)

    def test_second_enquiry_continues_the_open_thread(self):
        e = self.thread()
        r = self.enquire(message='And parking?')
        self.assertEqual((r.status_code, r.data['id']), (200, str(e.pk)))
        self.assertEqual((e.messages.count(), Lead.objects.count()), (2, 1))
        S.close(e)
        self.assertEqual(self.enquire().status_code, 201)
        self.assertEqual(Lead.objects.count(), 1)

    def test_one_open_enquiry_per_listing_in_the_database(self):
        e = self.thread()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enquiry.objects.create(user=e.user, listing=e.listing, kind=e.kind, subject='x', consent_version='x',
                                   consent_via='x', consented_at=timezone.now(), last_message_at=timezone.now())

    def test_one_lead_per_organization(self):
        self.thread()
        self.enquire(listing=self.live(make_property(org=self.org)))
        self.enquire(listing=self.live(make_property(org=self.other_org)))
        self.assertEqual(Lead.objects.filter(user=self.buyer, organization=self.org).count(), 1)
        self.assertEqual(Lead.objects.filter(user=self.buyer).count(), 2)

    def test_listing_agent_is_assigned_only_when_a_member(self):
        p = self.live(make_property(org=self.org), listed_by_agent=self.agent)
        self.assertEqual(self.thread(listing=p).lead.assigned_agent, self.agent)
        Enquiry.objects.all().delete()
        Lead.objects.all().delete()
        OrganizationMembership.objects.filter(user=self.ua).update(is_active=False)
        lead = self.thread(listing=p).lead
        self.assertEqual((lead.assigned_agent, lead.routing_state), (None, 'org_queue'))

    def test_refusals(self):
        seller = make_client()
        mine = self.live(make_property(owner=seller))
        self.assertEqual(self.enquire(user=seller, listing=mine).status_code, 409)
        self.assertEqual(self.enquire(user=self.ua).status_code, 409)             # own organization
        self.org.is_active = False
        self.org.save()
        self.assertIn(self.enquire().status_code, (404, 409))

    def test_blocked_people(self):
        e = self.thread()
        Lead.objects.filter(pk=e.lead_id).update(status='blocked_malicious')
        S.close(e)
        self.assertEqual(self.enquire().status_code, 403)
        from apps.whatsapp.models import WhatsAppSession
        bad = make_client()
        WhatsAppSession.objects.create(user=bad, phone=bad.phone, conversation_mode='BLOCKED')
        self.assertEqual(self.enquire(user=bad).status_code, 403)

    def test_daily_limit(self):
        self.thread()
        with patch.object(S, 'MAX_PER_DAY', 1):
            p = self.live(make_property(org=self.org))
            self.assertEqual(self.enquire(listing=p).status_code, 429)
            self.assertEqual(self.enquire().status_code, 200)

    def test_requirement(self):
        admin = make_user(role='admin')
        r = self.enquire(listing_id=None, requirement={'intent': 'rent', 'city': 'Lahore', 'details': '2 bed'})
        e = Enquiry.objects.get(pk=r.data['id'])
        self.assertEqual((e.kind, e.organization, e.lead.intent, e.lead.city_interest, e.lead.routing_state),
                         ('platform', None, 'rent', 'Lahore', 'ai_queue'))
        self.assertEqual(self.read(admin, e).status_code, 200)
        self.assertEqual(self.read(self.owner, e).status_code, 404)

    def test_idempotency_key(self):
        c, body = self.api(self.buyer), {'listing_id': str(self.p.pk), 'message': 'Hi', 'contact_consent': True}
        for _ in range(2):
            self.assertEqual(c.post(ENQ, body, format='json', HTTP_IDEMPOTENCY_KEY='k1').status_code, 201)
        self.assertEqual(ConversationMessage.objects.count(), 1)


class AccessTest(Base):
    def test_matrix(self):
        e = self.thread()
        viewer = make_user(role='agent')
        make_membership(viewer, self.org, role=ROLE.VIEWER)
        manager = make_user(role='agent')
        make_membership(manager, self.org, role=ROLE.SALES_MANAGER)
        expected = [(self.buyer, 200), (self.owner, 200), (manager, 200), (self.ua, 404), (viewer, 404),
                    (self.other, 404), (make_client(), 404), (make_user(role='admin'), 404)]
        for user, code in expected:
            self.assertEqual(self.read(user, e).status_code, code, user.phone)
            for path in ('', 'read/', 'close/', 'sharing/', 'messages/'):
                if code == 404:
                    c = self.api(user)
                    got = c.get(f'{THR}{e.pk}/') if not path else c.post(f'{THR}{e.pk}/{path}', {'share': True, 'body': 'x'})
                    self.assertEqual(got.status_code, 404, path)
        e.refresh_from_db()
        self.assertEqual((e.status, e.responder_shared_at), ('open', None))

    def test_only_the_assigned_agent(self):
        e = self.thread()
        Lead.objects.filter(pk=e.lead_id).update(assigned_agent=self.agent)
        self.assertEqual((self.read(self.ua, e).status_code, self.read(self.ub, e).status_code), (200, 404))
        OrganizationMembership.objects.filter(user=self.ua).update(is_active=False)
        self.assertEqual(self.read(self.ua, e).status_code, 404)

    def test_lists(self):
        e = self.thread()
        ids = lambda user, side: [t['id'] for t in self.api(user).get(THR, {'side': side}).data['results']]
        self.assertEqual((ids(self.buyer, 'user'), ids(self.buyer, 'responder')), ([str(e.pk)], []))
        self.assertEqual((ids(self.owner, 'user'), ids(self.owner, 'responder')), ([], [str(e.pk)]))
        self.assertEqual((ids(self.other, 'responder'), ids(self.ua, 'responder')), ([], []))
        self.assertEqual(self.api(self.buyer).get(THR, {'side': 'x'}).status_code, 400)
        self.assertEqual(len(self.api(self.buyer).get(ENQ).data['results']), 1)

    def test_private_owner_and_freelancer(self):
        seller = make_client(name='Seller')
        fu, fa = make_agent(is_active=True)
        for who, p in ((seller, make_property(owner=seller)),
                       (fu, make_property(listing_owner_type='freelance_agent', listed_by_agent=fa))):
            e = self.thread(listing=self.live(p))
            self.assertIsNone(e.lead.organization)
            self.assertEqual((self.read(who, e).status_code, self.say(who, e).status_code), (200, 201))
            self.assertEqual(self.read(self.owner, e).status_code, 404)
        self.assertEqual(Lead.objects.filter(user=self.buyer).count(), 2)     # never shared between listers


class ThreadTest(Base):
    def bodies(self, user, e):
        return [m['body'] for m in self.read(user, e).data]

    def test_conversation_and_unread(self):
        e = self.thread()
        t = lambda u: self.api(u).get(f'{THR}{e.pk}/').data
        self.assertEqual((t(self.owner)['unread'], t(self.buyer)['unread']), (True, False))
        self.say(self.owner, e)
        self.assertEqual((t(self.owner)['unread'], t(self.buyer)['unread']), (False, True))
        self.api(self.owner).post(f'{THR}{e.pk}/read/')
        self.api(self.buyer).post(f'{THR}{e.pk}/read/')
        self.assertEqual((t(self.owner)['unread'], t(self.buyer)['unread']), (False, False))
        self.assertEqual([(m['side'], m['body']) for m in self.read(self.buyer, e).data],
                         [('responder', 'Yes'), ('user', 'Is it available?')])
        e.lead.refresh_from_db()
        self.assertIsNotNone(e.lead.last_contacted_at)

    def test_contacts_are_masked_until_both_share(self):
        e = self.thread(message='Call me 0321-5550001')
        self.say(self.owner, e, 'Mine is 0300 9998887')
        self.assertIn('Call me [contact hidden]', self.bodies(self.owner, e))
        self.assertIn('Mine is [contact hidden]', self.bodies(self.buyer, e))
        self.assertIn('Call me 0321-5550001', self.bodies(self.buyer, e))          # own words
        for user in (self.owner, self.buyer):
            self.assertIsNone(self.api(user).get(f'{THR}{e.pk}/').data['counterpart']['phone'])
        self.assertIsNone(self.api(self.owner).get(f'{THR}{e.pk}/').data['counterpart']['name'])
        r = self.api(self.owner).post(f'{THR}{e.pk}/sharing/', {'share': True}, format='json')
        self.assertEqual((r.data['contacts_shared'], r.data['counterpart']),
                         (True, {'name': 'Buyer Name', 'phone': self.buyer.phone}))
        self.assertIn('Mine is 0300 9998887', self.bodies(self.buyer, e))
        self.assertEqual(self.api(self.buyer).get(f'{THR}{e.pk}/').data['counterpart'],
                         {'name': 'Prime', 'phone': '+923001110000'})
        self.api(self.buyer).post(f'{THR}{e.pk}/sharing/', {'share': False}, format='json')
        self.assertIsNone(self.api(self.owner).get(f'{THR}{e.pk}/').data['counterpart']['phone'])
        self.assertIn('Call me [contact hidden]', self.bodies(self.owner, e))

    def test_closed(self):
        e = self.thread()
        self.assertEqual(self.api(self.owner).post(f'{THR}{e.pk}/close/').data['status'], 'closed')
        self.assertEqual(self.say(self.buyer, e).status_code, 409)

    def test_messages_are_immutable(self):
        e = self.thread()
        for method in ('put', 'patch', 'delete'):
            self.assertEqual(getattr(self.api(self.buyer), method)(f'{THR}{e.pk}/messages/').status_code, 405)

    def test_thread_survives_lead_and_listing_removal(self):
        e = self.thread()
        Lead.objects.all().delete()
        self.p.delete()
        self.assertEqual(self.say(self.buyer, e, 'Still there?').status_code, 201)
        self.assertEqual(self.read(self.owner, e).status_code, 200)

    def test_throttle(self):
        e = self.thread()
        with patch('apps.enquiries.views.ThreadMessageThrottle.get_rate', return_value='1/min'):
            cache.clear()
            self.assertEqual([self.say(self.buyer, e).status_code for _ in range(2)], [201, 429])


class NotifyTest(Base):
    def emails(self):
        return list(OutboxMessage.objects.filter(kind='notification.email').order_by('created_at'))

    def test_manager_gets_one_notification_per_unread_burst(self):
        e = self.thread(message='SECRET 0321-5550001')
        self.say(self.buyer, e, 'again')
        n = Notification.objects.get(user=self.owner)
        self.assertNotIn('SECRET', n.message + n.title)
        self.assertNotIn(self.buyer.phone, n.message)
        self.assertEqual(Notification.objects.filter(user__in=[self.ua, self.ub, self.other]).count(), 0)
        S.mark_read(e, 'responder')
        self.say(self.buyer, e, 'third')
        self.assertEqual(Notification.objects.filter(user=self.owner).count(), 2)
        Enquiry.objects.update(user_last_at=timezone.now() - timedelta(hours=2))
        S.mark_read(e, 'user')
        self.say(self.buyer, e, 'much later')
        self.assertEqual(Notification.objects.filter(user=self.owner).count(), 3)

    def test_email_is_sent_through_the_outbox(self):
        self.thread()
        [row] = self.emails()
        outbox.dispatch(row.pk)
        row.refresh_from_db()
        [m] = mail.outbox
        self.assertEqual((m.to, m.subject, row.status, row.payload), (['boss@prime.example'], 'New enquiry', 'sent', '{}'))
        self.assertIn('https://app.test/threads/', m.body)
        self.assertNotIn('Is it available', m.body)

    def test_reply_uses_the_preferred_channel(self):
        e = self.thread(preferred_channel='email')
        OutboxMessage.objects.all().delete()
        self.say(self.owner, e)
        self.assertEqual(list(OutboxMessage.objects.values_list('kind', flat=True)), ['notification.email'])
        p = self.live(make_property(org=self.org))
        e = self.thread(listing=p)
        OutboxMessage.objects.all().delete()
        self.say(self.owner, e)
        self.assertFalse(OutboxMessage.objects.exists())                 # in_app: inbox only
        self.assertEqual(Notification.objects.filter(user=self.buyer).count(), 2)

    def test_preferences_and_missing_provider(self):
        UserNotificationPreference.objects.update_or_create(user=self.owner, defaults={'email_enabled': False})
        e = self.thread()
        self.assertEqual(self.emails(), [])
        UserNotificationPreference.objects.update(email_enabled=True)
        S.mark_read(e, 'responder')
        with override_settings(EMAIL_HOST=''):
            self.say(self.buyer, e)
        self.assertEqual(self.emails(), [])

    def test_opt_out_after_queueing(self):
        from apps.notifications.delivery import enqueue_email
        row = enqueue_email(self.buyer, 'Hi\nBcc: x@y.example', 'b', key='k')
        UserNotificationPreference.objects.update_or_create(user=self.buyer, defaults={'email_enabled': False})
        outbox.dispatch(row.pk)
        row.refresh_from_db()
        self.assertEqual((row.status, mail.outbox), ('failed', []))
        UserNotificationPreference.objects.update(email_enabled=True)
        outbox.dispatch(enqueue_email(self.buyer, 'Hi\nBcc: x@y.example', 'b', key='k2').pk)
        self.assertEqual((mail.outbox[0].subject, mail.outbox[0].recipients()),
                         ('Hi Bcc: x@y.example', ['buyer@mail.example']))


class CrmIsolationTest(Base):
    def test_whatsapp_chat_is_filed_under_its_own_organization(self):
        from apps.whatsapp.history import record
        from apps.whatsapp.models import WhatsAppSession
        upsert_lead(self.buyer, '', organization=self.org)
        self.enquire(listing=self.live(make_property(org=self.other_org)))      # the newest lead
        for org, name in ((self.org, 'Prime'), (None, None)):
            s = WhatsAppSession.objects.create(phone=f'{name}', user=self.buyer, organization=org)
            record(s, direction='inbound', body='private', wa_message_id=f'w{name}')
        # T-3100b: both chats are logged once; only the organization's own one is on its lead
        rows = ConversationMessage.objects.filter(body='private')
        self.assertEqual(rows.count(), 2)
        self.assertEqual([m.lead.organization for m in rows.filter(lead__isnull=False)], [self.org])

    def test_platform_chat_never_lands_on_a_lister_lead(self):
        fu, fa = make_agent(is_active=True)
        self.enquire(listing=self.live(make_property(listing_owner_type='freelance_agent', listed_by_agent=fa)))
        from apps.whatsapp.history import record
        from apps.whatsapp.models import WhatsAppSession
        record(WhatsAppSession.objects.create(phone='p', user=self.buyer), direction='outbound', body='private',
               wa_message_id='w1')
        self.assertFalse(ConversationMessage.objects.filter(body='private', lead__isnull=False).exists())
        upsert_lead(self.buyer, 'buy', organization=self.org)
        self.assertEqual(Lead.objects.filter(user=self.buyer, organization=self.org).count(), 1)
        self.assertEqual(Enquiry.objects.get().lead.organization, None)

    def test_upsert_with_several_leads(self):
        self.thread()
        self.enquire(listing=self.live(make_property(org=self.other_org)))
        upsert_lead(self.buyer, 'invest', organization=self.org)
        self.assertEqual({l.organization: l.intent for l in Lead.objects.filter(user=self.buyer)},
                         {self.org: 'invest', self.other_org: 'buy'})

    def test_private_thread_stays_hidden_when_its_lead_is_given_to_an_organization(self):
        e = self.thread(listing=self.live(make_property(owner=make_client())))
        Lead.objects.filter(pk=e.lead_id).update(organization=self.org)
        r = self.api(self.owner).get(f'/api/v1/leads/{e.lead_id}/conversations/')
        self.assertEqual((r.status_code, r.data), (200, []))

    def test_own_thread_is_in_the_crm(self):
        e = self.thread()
        r = self.api(self.owner).get(f'/api/v1/leads/{e.lead_id}/conversations/')
        self.assertEqual([m['body'] for m in r.data], ['Is it available?'])

    def test_lead_merge_keeps_the_thread(self):
        e = self.thread()
        keep = Lead.objects.create(user=self.buyer, organization=self.org)
        r = self.api(make_user(role='admin')).post('/api/v1/leads/merge/', {
            'primary_id': str(keep.pk), 'secondary_id': str(e.lead_id)}, format='json')
        e.refresh_from_db()
        self.assertEqual((r.status_code, e.lead, e.messages.count()), (200, keep, 1))


class PrivacyTest(Base):
    def test_erasure(self):
        e = self.thread()
        self.say(self.owner, e)
        seller_thread = self.thread(user=self.other, listing=self.live(make_property(owner=self.buyer)))
        self.say(self.buyer, seller_thread, 'I am the buyer too')
        self.assertEqual(S.erase_user(self.buyer), 1)
        self.assertFalse(Enquiry.objects.filter(user=self.buyer).exists())
        self.assertEqual(set(ConversationMessage.objects.values_list('body', flat=True)),
                         {'Is it available?', '[REDACTED]'})

    def test_erasure_service_runs_it(self):
        from apps.compliance.erasure import RTBFOrchestrator
        from apps.compliance.models import DataDeletionRequest
        self.thread()
        req = DataDeletionRequest.objects.create(user=self.buyer)
        RTBFOrchestrator().execute(req)
        req.refresh_from_db()
        self.assertEqual((req.erasure_scope['enquiries_deleted'], Enquiry.objects.count()), (1, 0))


class ToolTest(Base):
    def propose(self, user='buyer', **args):
        set_context(self.buyer if user == 'buyer' else user, self.buyer.phone)
        return confirmation.propose(R.REGISTRY['create_enquiry'],
                                    {'listing': self.p.ref_no, 'message': 'Can I visit?', **args})

    def test_nothing_is_sent_before_the_yes(self):
        r = self.propose()
        self.assertFalse(Enquiry.objects.exists() or Lead.objects.exists())
        for shown in ('Corner House', 'Can I visit?', 'Prime', S.CONSENT_TEXT):
            self.assertIn(shown, r['message'])
        reply = confirmation.resolve_reply(self.buyer.phone, 'yes', user=self.buyer)
        self.assertIn('sent to Prime', reply)
        e = Enquiry.objects.get()
        self.assertEqual((e.user, e.consent_via, e.listing, e.messages.get().body),
                         (self.buyer, 'assistant', self.p, 'Can I visit?'))
        self.assertEqual(PendingAction.objects.get().result['enquiry_id'], str(e.pk))

    def test_the_stored_listing_is_an_id(self):
        import json
        self.propose()
        self.assertEqual(json.loads(PendingAction.objects.get().args)['listing'], str(self.p.pk))

    def test_direct_call_only_proposes(self):
        from apps.ai.tools import create_enquiry
        set_context(self.buyer, self.buyer.phone)
        self.assertTrue(create_enquiry(self.p.ref_no, 'hi')['confirmation_required'])
        self.assertFalse(Enquiry.objects.exists())

    def test_refusals(self):
        for kw in ({'listing': 'nope'}, {'listing': '7'}, {'message': ' '}, {'user': None}, {'user': self.owner}):
            self.assertIn('error', self.propose(**kw), kw)
        self.assertFalse(PendingAction.objects.exists())

    def test_listing_withdrawn_before_the_yes(self):
        self.propose()
        Property.objects.filter(pk=self.p.pk).update(is_active=False)
        self.assertIn('no longer available', confirmation.resolve_reply(self.buyer.phone, 'yes', user=self.buyer))
        self.assertFalse(Enquiry.objects.exists())
