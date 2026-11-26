"""T-3100b — one message log for WhatsApp, readers on both logs, backfill (ADR-064 phase B)."""
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.leads.models import ConversationMessage as CM, UnifiedMessage
from apps.whatsapp.history import record, session_log
from apps.whatsapp.models import WhatsAppMessage, WhatsAppSession
from apps.whatsapp.router import MessageRouter
from tests.factories import make_client, make_developer, make_lead, make_org
from tests.test_runtime import LOCMEM


def sent(wa_id='wamid.OUT'):
    client = MagicMock()
    client.send_text.return_value = {'messages': [{'id': wa_id}]} if wa_id else {}
    return client


@override_settings(CACHES=LOCMEM)
class WriterTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.person = make_client()
        self.lead = make_lead(self.person, self.org)
        self.s = WhatsAppSession.objects.create(phone=self.person.phone.lstrip('+'), user=self.person,
                                                organization=self.org)

    def test_inbound_is_one_row_and_redelivery_updates_it(self):
        data = {'id': 'wamid.IN1', 'audio': {'id': 'media-1'}}
        MessageRouter._log_inbound(data, self.s, '[voice] hello', 'audio')
        MessageRouter._log_inbound(data, self.s, '[voice] hello again', 'audio')
        row = CM.objects.get()
        self.assertEqual((row.session_id, row.lead_id, row.organization_id, row.msg_type, row.media_id, row.body),
                         (self.s.pk, self.lead.pk, self.org.pk, 'audio', 'media-1', '[voice] hello again'))
        self.assertFalse(WhatsAppMessage.objects.exists())                  # the old log is not written
        self.assertEqual(session_log(self.s).count(), 1)

    def test_outbound_reply_and_fallback_ids(self):
        with patch('apps.whatsapp.router.get_wa_client', return_value=sent()):
            MessageRouter._send_and_log('923', 'reply', self.s, org=self.org)
        with patch('apps.whatsapp.router.get_wa_client', return_value=sent('')):
            MessageRouter._send_and_log('923', 'buffered 1', self.s, org=self.org)
            MessageRouter._send_and_log('923', 'buffered 2', self.s, org=self.org)
        broken = MagicMock()
        broken.send_text.side_effect = RuntimeError('down')
        with patch('apps.whatsapp.router.get_wa_client', return_value=broken):
            MessageRouter._send_and_log('923', 'never sent', self.s, org=self.org)
        rows = list(CM.objects.order_by('created_at'))
        self.assertEqual([r.body for r in rows], ['reply', 'buffered 1', 'buffered 2'])
        self.assertEqual(rows[0].wa_message_id, 'wamid.OUT')
        self.assertTrue(rows[1].wa_message_id.startswith('out-'))
        self.assertNotEqual(rows[1].wa_message_id, rows[2].wa_message_id)
        self.assertEqual({r.sender_id for r in rows}, {None})

    def test_agent_live_chat_and_dashboard_send_reach_the_log(self):
        from apps.whatsapp.consumers import AgentRoomConsumer
        consumer = AgentRoomConsumer()
        consumer.user = self.owner
        with patch('apps.whatsapp.client.get_wa_client', return_value=sent('')):
            async_to_sync(consumer._send_whatsapp)(str(self.s.pk), 'from the agent')
            async_to_sync(consumer._send_whatsapp)(str(self.s.pk), 'again')
        api = APIClient()
        api.force_authenticate(self.owner)
        with patch('apps.whatsapp.client.get_wa_client', return_value=sent('wamid.D1')):
            r = api.post(f'/api/v1/leads/{self.lead.pk}/send-message/', {'body': 'from the dashboard'})
        self.assertEqual(r.status_code, 201, r.data)
        log = {m.body: m for m in session_log(self.s)}
        self.assertEqual(set(log), {'from the agent', 'again', 'from the dashboard'})
        self.assertEqual({m.sender_id for m in log.values()}, {self.owner.pk})
        r = api.get(f'/api/v1/leads/{self.lead.pk}/messages/')
        self.assertEqual({m['author'] for m in r.data['results']}, {'team'})

    def test_window_check_sees_both_logs(self):
        from apps.whatsapp.client import is_within_24h_window
        phone = self.person.phone
        self.assertFalse(is_within_24h_window(phone))
        old = WhatsAppMessage.objects.create(session=self.s, wa_message_id='old1', direction='inbound', body='hi')
        self.assertTrue(is_within_24h_window(phone))                       # a message from before the switch
        WhatsAppMessage.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=30))
        self.assertFalse(is_within_24h_window(phone))
        record(self.s, direction='inbound', body='new', wa_message_id='new1')
        self.assertTrue(is_within_24h_window(phone))

    def test_reengagement_reads_only_the_organizations_chat(self):
        from apps.campaigns.re_engagement import LeadContextBuilder
        other = WhatsAppSession.objects.create(phone=self.person.phone.lstrip('+'), user=self.person,
                                               organization=make_org(name='Other'))
        record(other, direction='inbound', body='secret for Other', wa_message_id='x1')
        record(self.s, direction='inbound', body='for Prime', wa_message_id='x2')
        text = LeadContextBuilder()._wa_transcript(self.lead)
        self.assertIn('for Prime', text)
        self.assertNotIn('secret for Other', text)

    def test_erasure_removes_the_log_rows(self):
        from apps.compliance import erasure as E
        record(self.s, direction='inbound', body='mine', wa_message_id='e1', media_url='https://x.example/a.jpg')
        nobody = WhatsAppSession.objects.create(phone=self.person.phone, organization=self.org)   # not linked yet
        record(nobody, direction='inbound', body='before sign-up', wa_message_id='e2')
        E._whatsapp(self.person)
        self.assertFalse(CM.objects.filter(body__in=['mine', 'before sign-up']).exists())


@override_settings(CACHES=LOCMEM)
class BackfillTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.person = make_client()
        self.lead = make_lead(self.person, self.org)
        self.s = WhatsAppSession.objects.create(phone='92300', user=self.person, organization=self.org)

    def old(self, wid, body, session=None, at=None):
        w = WhatsAppMessage.objects.create(session=session or self.s, wa_message_id=wid, direction='inbound',
                                           body=body, msg_type='image', media_url='https://x.example/i.jpg')
        if at:
            WhatsAppMessage.objects.filter(pk=w.pk).update(created_at=at)
        return w

    def bodies(self):
        return sorted(m.body for m in UnifiedMessage.objects.all())

    def test_links_mirrors_copies_the_rest_and_is_idempotent(self):
        then = timezone.now() - timedelta(days=40)
        self.old('a', 'mirrored')
        mirror = CM.objects.create(lead=self.lead, direction='inbound', channel='whatsapp', body='mirrored',
                                   wa_message_id='a')
        self.old('b', 'only in the old log', at=then)
        other = WhatsAppSession.objects.create(phone='92300', user=self.person, organization=make_org(name='O'))
        self.old('c', 'misfiled', session=other)
        misfiled = CM.objects.create(lead=self.lead, direction='inbound', channel='whatsapp', body='misfiled',
                                     wa_message_id='c')                       # KI-073
        before = self.bodies()
        self.assertEqual(before, ['mirrored', 'misfiled', 'only in the old log'])
        with self.assertRaises(CommandError):
            call_command('backfill_contact_messages', '--check', stdout=StringIO())
        out = StringIO()
        call_command('backfill_contact_messages', '--batch', '2', stdout=out)
        self.assertIn('linked=1 copied=2 pending=0', out.getvalue())
        self.assertEqual(self.bodies(), before)                              # each message still once
        mirror.refresh_from_db()
        self.assertEqual((mirror.session_id, mirror.lead_id, mirror.msg_type, mirror.media_url),
                         (self.s.pk, self.lead.pk, 'image', 'https://x.example/i.jpg'))
        copy = CM.objects.get(wa_message_id='b')
        self.assertEqual((copy.lead_id, copy.organization_id, copy.created_at), (None, self.org.pk,
                         WhatsAppMessage.objects.get(wa_message_id='b').created_at))
        misfiled.refresh_from_db()
        self.assertIsNone(misfiled.session_id)                               # left as it was, still hidden
        self.assertEqual(CM.objects.get(wa_message_id='c', session=other).lead_id, None)
        self.assertEqual({m.source for m in UnifiedMessage.objects.all()}, {'crm'})
        call_command('backfill_contact_messages', stdout=StringIO())
        self.assertEqual(CM.objects.count(), 4)
        call_command('backfill_contact_messages', '--check', stdout=StringIO())

    def test_live_chat_history_endpoint_reads_both(self):
        self.old('h1', 'old message')
        record(self.s, direction='outbound', body='new reply', wa_message_id='h2')
        api = APIClient()
        admin = make_client()
        admin.role = 'admin'
        admin.is_staff = True
        admin.save()
        api.force_authenticate(admin)
        r = api.get(f'/api/v1/whatsapp/history/{self.s.pk}/')
        self.assertEqual(r.status_code, 200, getattr(r, 'data', r))
        self.assertEqual([m['body'] for m in r.data['messages']['results']], ['old message', 'new reply'])
