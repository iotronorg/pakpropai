"""T-3100 — unified contact (person ↔ leads) and unified messages (FR-CRM-001, GAP-016, ADR-064)."""
import importlib
from unittest.mock import MagicMock, patch

from django.apps import apps as django_apps
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.enquiries.models import Enquiry
from apps.leads import contacts as C
from apps.leads.models import ConversationMessage, CRMContact, Lead, UnifiedMessage
from apps.whatsapp.models import WhatsAppMessage, WhatsAppSession
from tests.factories import make_agent, make_client, make_developer, make_lead, make_org, make_user
from tests.test_runtime import LOCMEM

BASE = '/api/v1/leads/'


def api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def wa(session, wid, body, direction='inbound'):
    return WhatsAppMessage.objects.create(session=session, wa_message_id=wid, direction=direction, body=body)


def crm(lead, body, wid='', direction='inbound', **kw):
    kw.setdefault('channel', 'whatsapp' if wid else 'dashboard')
    return ConversationMessage.objects.create(lead=lead, body=body, wa_message_id=wid, direction=direction, **kw)


def enquiry(lead, org, **kw):
    now = timezone.now()
    return Enquiry.objects.create(user=lead.user, kind='organization', organization=org, subject='s', lead=lead,
                                  consent_version='v1', consent_via='api', consented_at=now, last_message_at=now,
                                  **kw)


@override_settings(CACHES=LOCMEM)
class ViewTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.person = make_client()
        self.lead = make_lead(self.person, self.org)
        self.session = WhatsAppSession.objects.create(phone=self.person.phone.lstrip('+'), user=self.person,
                                                      organization=self.org)

    def bodies(self, lead=None):
        return sorted(m.body for m in C.lead_messages(lead or self.lead))

    def test_mirror_shows_once_and_gaps_are_filled(self):
        wa(self.session, 'w1', 'hello')
        crm(self.lead, 'hello', 'w1')                                   # router mirror of w1
        wa(self.session, 'w2', 'agent live chat', 'outbound')           # consumer: no CRM mirror
        crm(self.lead, 'from the dashboard', 'w3', 'outbound', sender=self.owner)   # send-message: no WA row
        self.assertEqual(self.bodies(), ['agent live chat', 'from the dashboard', 'hello'])
        self.assertEqual(UnifiedMessage.objects.get(body='hello').source, 'crm')

    def test_misfiled_mirror_stays_with_its_own_organization(self):
        other = make_org(name='Other')
        other_lead = make_lead(self.person, other)
        theirs = WhatsAppSession.objects.create(phone='x', user=self.person, organization=other)
        wa(theirs, 'w9', 'meant for Other')
        crm(self.lead, 'meant for Other', 'w9')                         # KI-073: filed under Prime's lead
        self.assertEqual(self.bodies(), [])
        self.assertEqual(self.bodies(other_lead), ['meant for Other'])

    def test_unlinked_session_by_phone_and_other_threads(self):
        unlinked = WhatsAppSession.objects.create(phone=self.person.phone, organization=self.org)
        wa(unlinked, 'w4', 'before sign-up')
        WhatsAppSession.objects.create(phone=self.person.phone.lstrip('+'), organization=make_org(name='Z'))
        foreign = enquiry(self.lead, make_org(name='Lister'))
        crm(self.lead, 'other lister thread', enquiry=foreign, channel='in_app')
        crm(self.lead, 'note')
        self.assertEqual(self.bodies(), ['before sign-up', 'note'])

    def test_link_contacts_migration_step(self):
        mod = importlib.import_module('apps.leads.migrations.0027_unified_contact_messages')
        old = CRMContact.objects.create(organization=self.org, phone=self.person.phone.lstrip('+'))
        unknown = CRMContact.objects.create(organization=self.org, phone='+15550001111')
        mod.link_contacts(django_apps, None)
        old.refresh_from_db()
        unknown.refresh_from_db()
        self.assertEqual((old.user_id, unknown.user_id), (self.person.pk, None))


@override_settings(CACHES=LOCMEM)
class ApiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.agent_user, self.agent = make_agent(org=self.org)
        self.person = make_client(name='Sara Khan')
        self.lead1 = make_lead(self.person, self.org, intent='buy', assigned_agent=self.agent)
        self.lead2 = make_lead(self.person, self.org, intent='rent')
        self.other = make_lead(make_client(name='Bilal'), self.org)
        make_lead(make_client(name='Elsewhere'), make_org(name='Other'))

    def test_list_groups_people_and_agents_see_their_own(self):
        r = api(self.owner).get(BASE + 'contacts/')
        self.assertEqual(r.status_code, 200)
        rows = {x['name']: x for x in r.data['results']}
        self.assertEqual(set(rows), {'Sara Khan', 'Bilal'})
        self.assertEqual((rows['Sara Khan']['leads'], rows['Sara Khan']['phone']), (2, self.person.phone))
        r = api(self.agent_user).get(BASE + 'contacts/')
        self.assertEqual([x['name'] for x in r.data['results']], ['Sara Khan'])
        self.assertEqual(r.data['results'][0]['leads'], 1)                       # only the lead assigned
        self.assertEqual(api(self.agent_user).get(BASE + f'contacts/{self.other.user_id}/').status_code, 404)
        self.assertEqual(api(make_client()).get(BASE + 'contacts/').status_code, 403)

    def test_search_tags_and_withheld_phone(self):
        digits = self.person.phone[-6:]
        self.assertEqual(len(api(self.owner).get(BASE + f'contacts/?search={digits}').data['results']), 1)
        self.assertEqual(len(api(self.owner).get(BASE + 'contacts/?search=sara').data['results']), 1)
        enquiry(self.lead1, self.org, consent_revoked_at=timezone.now())
        enquiry(self.lead2, self.org, consent_revoked_at=timezone.now())
        self.assertEqual(api(self.owner).get(BASE + f'contacts/?search={digits}').data['results'], [])
        row = api(self.owner).get(BASE + 'contacts/?search=sara').data['results'][0]
        self.assertEqual((row['phone'], row['contact_withheld']), (None, True))
        r = api(self.owner).get(BASE + f'contacts/{self.person.pk}/')
        self.assertEqual((r.data['phone'], len(r.data['leads'])), (None, 2))

    def test_detail_and_notes(self):
        r = api(self.owner).get(BASE + f'contacts/{self.person.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual({x['intent'] for x in r.data['leads']}, {'buy', 'rent'})
        self.assertEqual(r.data['leads'][0]['stage']['key'], 'new')
        url = BASE + f'contacts/{self.person.pk}/'
        r = api(self.owner).patch(url, {'name': 'Sara (investor)', 'tags': ['vip', 'VIP', 'dha']}, format='json')
        self.assertEqual((r.status_code, r.data['name'], r.data['tags'], r.data['account_name']),
                         (200, 'Sara (investor)', ['vip', 'dha'], 'Sara Khan'))
        self.assertTrue(AuditEvent.objects.filter(action='contact.updated').exists())
        self.assertEqual(CRMContact.objects.get(user=self.person).phone, '')          # no copy of the phone
        self.assertEqual([x['id'] for x in api(self.owner).get(BASE + 'contacts/?tag=vip').data['results']],
                         [str(self.person.pk)])
        self.assertEqual(len(api(self.owner).get(BASE + 'contacts/?search=investor').data['results']), 1)
        self.assertEqual(api(self.agent_user).patch(url, {'name': 'x'}, format='json').status_code, 403)
        for bad in ({'tags': 'vip'}, {'tags': ['']}, {'name': 'x' * 101}, {'phone': '1'}):
            self.assertEqual(api(self.owner).patch(url, bad, format='json').status_code, 400, bad)
        self.assertEqual(api(self.owner).patch(BASE + f'contacts/{make_client().pk}/', {'name': 'x'},
                                               format='json').status_code, 404)

    def test_messages_paging(self):
        s = WhatsAppSession.objects.create(phone=self.person.phone, user=self.person, organization=self.org)
        for i in range(5):
            wa(s, f'p{i}', f'wa {i}')
        crm(self.lead2, 'note on lead 2', sender=self.owner, direction='outbound')
        url = BASE + f'contacts/{self.person.pk}/messages/?limit=4'
        r = api(self.owner).get(url)
        self.assertEqual((len(r.data['results']), r.data['results'][0]['body']), (4, 'note on lead 2'))
        self.assertEqual((r.data['results'][0]['author'], r.data['results'][0]['sender_name']),
                         ('team', self.owner.name))
        r2 = api(self.owner).get(url + '&before=' + r.data['next'])
        self.assertEqual(([m['body'] for m in r2.data['results']], r2.data['next']), (['wa 1', 'wa 0'], None))
        ids = {m['id'] for m in r.data['results']} | {m['id'] for m in r2.data['results']}
        self.assertEqual(len(ids), 6)
        self.assertEqual(api(self.owner).get(url + '&before=nonsense').status_code, 400)
        # the agent sees the chat and only their lead's CRM log
        r = api(self.agent_user).get(BASE + f'contacts/{self.person.pk}/messages/')
        self.assertEqual([m['body'] for m in r.data['results'] if m['source'] == 'crm'], [])
        self.assertEqual(len(r.data['results']), 5)
        r = api(self.owner).get(BASE + f'{self.lead1.pk}/messages/')
        self.assertEqual((r.status_code, len(r.data['results'])), (200, 5))

    def test_summary_reads_the_whatsapp_chat(self):
        s = WhatsAppSession.objects.create(phone=self.person.phone, user=self.person, organization=self.org)
        wa(s, 'q1', 'I want a 10 marla house in DHA')
        with patch('apps.ai.llm.complete', return_value=MagicMock(text='Wants a house.')) as m:
            r = api(self.owner).post(BASE + f'{self.lead1.pk}/summarize/')
        self.assertEqual((r.status_code, r.data['message_count']), (200, 1))
        self.assertIn('10 marla house', m.call_args[0][1]['transcript'])
