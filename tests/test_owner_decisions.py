"""Owner decisions of 2026-11-24: private data exports (KI-066), withheld contacts in the CRM (KI-074)."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.re_engagement import ReEngagementScanner
from apps.compliance.models import DataExportFile, DataExportRequest
from apps.compliance.tasks import generate_data_export, purge_data_exports
from apps.conversations import store
from apps.enquiries import services as E
from apps.leads import privacy
from apps.leads.models import Appointment, Lead, LeadActivity
from apps.properties.models import Property
from tests.factories import make_agent, make_client, make_developer, make_property
from tests.test_runtime import LOCMEM


def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@override_settings(CACHES=LOCMEM)
class ExportTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.other = make_client(), make_client()
        conv = store.open_whatsapp(self.user.phone.lstrip('+'), user=self.user)
        store.add_exchange(self.user.phone.lstrip('+'), None, self.user, 'my secret wish', 'noted')
        self.conv = conv
        self.req = DataExportRequest.objects.create(user=self.user)

    def url(self, tail='download/'):
        return f'/api/v1/compliance/export/{self.req.pk}/{tail}'

    def test_download_is_private_and_has_the_chats(self):
        with patch('cloudinary.uploader.upload') as upload:
            generate_data_export.apply(args=[str(self.req.pk)])
        upload.assert_not_called()
        r = client(self.user).get(self.url())
        self.assertEqual((r.status_code, r['Cache-Control']), (200, 'private, no-store'))
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertEqual([m['text'] for m in json.loads(r.content)['assistant_messages']], ['my secret wish', 'noted'])
        self.assertEqual(client(self.other).get(self.url()).status_code, 404)
        self.assertEqual(client(self.other).get(self.url('')).status_code, 404)
        self.assertEqual(APIClient().get(self.url()).status_code, 401)
        self.assertEqual(client(self.user).get(self.url('')).data['status'], 'ready')

    def test_other_peoples_chats_are_not_included(self):
        store.add_exchange(self.other.phone.lstrip('+'), None, self.other, 'not yours', 'ok')
        generate_data_export.apply(args=[str(self.req.pk)])
        self.assertNotIn('not yours', self.req.file.payload)

    def test_encrypted_at_rest(self):
        generate_data_export.apply(args=[str(self.req.pk)])
        with connection.cursor() as cur:
            cur.execute('SELECT payload FROM compliance_data_export_files')
            self.assertNotIn('secret wish', cur.fetchone()[0])

    def test_expiry(self):
        generate_data_export.apply(args=[str(self.req.pk)])
        DataExportFile.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(client(self.user).get(self.url()).status_code, 404)
        self.assertEqual(purge_data_exports(), 1)
        self.req.refresh_from_db()
        self.assertEqual((self.req.status, DataExportFile.objects.exists()), ('expired', False))


@override_settings(CACHES=LOCMEM)
class WithheldTest(TestCase):
    def setUp(self):
        cache.clear()
        self.dev, self.org = make_developer()
        _, self.agent = make_agent(org=self.org)
        p = make_property(org=self.org)
        Property.objects.filter(pk=p.pk).update(is_sandbox=False)
        p.refresh_from_db()
        self.listing = p
        self.user = make_client()
        self.enquiry, _ = E.create(self.user, message='Hello', listing=p)
        self.lead = self.enquiry.lead
        Lead.objects.filter(pk=self.lead.pk).update(assigned_agent=self.agent)

    def stop(self):
        E.set_sharing(self.enquiry, E.USER, False)

    def get(self, path):
        return client(self.dev).get(f'/api/v1/leads/{path}')

    def test_lead_phone(self):
        r = self.get(f'{self.lead.pk}/').data
        self.assertEqual((r['phone'], r['contact_withheld']), (self.user.phone, False))
        self.stop()
        r = self.get(f'{self.lead.pk}/')
        self.assertEqual((r.data['phone'], r.data['contact_withheld'], r.data['wa_session_id']), (None, True, None))
        digits = self.user.phone.lstrip('+')
        for path in ('', f'{self.lead.pk}/', f'{self.lead.pk}/conversations/', f'{self.lead.pk}/activities/'):
            self.assertNotIn(digits, self.get(path).content.decode(), path)
        E.set_sharing(self.enquiry, E.USER, True)
        self.assertEqual(self.get(f'{self.lead.pk}/').data['phone'], self.user.phone)

    def test_another_consent_keeps_it(self):
        p = make_property(org=self.org, title='Second')
        E.create(self.user, message='And this one', listing=p)
        self.stop()
        self.assertEqual(self.get(f'{self.lead.pk}/').data['phone'], self.user.phone)

    def test_leads_without_enquiries_are_unchanged(self):
        lead = Lead.objects.create(user=make_client(), organization=self.org)
        self.assertEqual(privacy.phone(lead), lead.user.phone)

    def test_staff_cannot_write_by_whatsapp(self):
        self.stop()
        with patch('apps.whatsapp.client.get_wa_client') as wa:
            r = client(self.dev).post(f'/api/v1/leads/{self.lead.pk}/send-message/', {'body': 'hi'}, format='json')
        self.assertEqual(r.status_code, 409)
        wa.assert_not_called()

    def test_appointment_and_activity(self):
        from apps.leads.serializers import AppointmentSerializer, LeadActivitySerializer
        a = Appointment.objects.create(lead=self.lead, property=self.listing, agent=self.agent,
                                       scheduled_at=timezone.now() + timedelta(days=1))
        act = LeadActivity.objects.filter(lead=self.lead, actor=self.user).first()
        self.assertEqual(AppointmentSerializer(a).data['lead_phone'], self.user.phone)
        self.stop()
        a, act = Appointment.objects.get(pk=a.pk), LeadActivity.objects.get(pk=act.pk)
        self.assertIsNone(AppointmentSerializer(a).data['lead_phone'])
        self.assertIsNone(LeadActivitySerializer(act).data['actor_phone'])

    def test_agent_texts(self):
        self.stop()
        self.assertEqual(privacy.phone_text(Lead.objects.get(pk=self.lead.pk)), 'number not shared')

    def test_automated_outreach_skips_the_person(self):
        Lead.objects.filter(pk=self.lead.pk).update(status='qualified',
                                                    last_contacted_at=timezone.now() - timedelta(days=60))
        with patch('apps.config.services.SystemConfigService.get', return_value='true'):
            self.assertEqual(ReEngagementScanner().find_cold_leads().count(), 1)
            self.stop()
            self.assertEqual(ReEngagementScanner().find_cold_leads().count(), 0)

    def test_campaign_recipient_is_skipped(self):
        from unittest.mock import MagicMock

        from apps.campaigns.campaign_manager import CampaignOrchestrator
        wa, recipient = MagicMock(), MagicMock(lead=self.lead)
        self.stop()
        CampaignOrchestrator().dispatch_one(recipient, wa, MagicMock())
        self.assertEqual(recipient.delivery_status, 'skipped')
        wa.send_text.assert_not_called()
        wa.send_template.assert_not_called()

    def test_session_phones(self):
        mine = Lead.objects.filter(organization=self.org)
        self.assertEqual(privacy.withheld_phones(mine), set())
        self.stop()
        self.assertEqual(privacy.withheld_phones(mine), {self.user.phone, self.user.phone.lstrip('+')})

    def test_live_chat_streams_hide_the_number(self):
        """KI-083: the agent room's inbound events and its session list follow the same rule."""
        from unittest.mock import AsyncMock, MagicMock

        from asgiref.sync import async_to_sync

        from apps.whatsapp.consumers import AgentRoomConsumer
        from apps.whatsapp.models import WhatsAppSession
        from apps.whatsapp.router import MessageRouter
        digits = self.user.phone.lstrip('+')
        session = WhatsAppSession.objects.create(phone=digits, user=self.user, organization=self.org,
                                                 conversation_mode='AGENT_MANAGED')
        other = WhatsAppSession.objects.create(phone='923001234567', organization=self.org,
                                               conversation_mode='AGENT_MANAGED')
        room = AgentRoomConsumer()
        room.org_id = str(self.org.pk)

        def streamed():
            layer = MagicMock(group_send=AsyncMock())
            with patch('channels.layers.get_channel_layer', return_value=layer):
                MessageRouter._broadcast_to_agent_room(
                    {'from': digits, 'id': 'wamid.1', 'type': 'text', 'text': {'body': 'hi'}}, session, self.org, 'text')
            group, event = layer.group_send.await_args.args
            self.assertEqual(group, f'org_{self.org.pk}_agent_room')
            listed = {x['session_id']: x['phone'] for x in async_to_sync(room._get_active_sessions)()}
            return event, listed

        event, listed = streamed()
        self.assertEqual((event['phone'], event['message']['from']), (digits, digits))
        self.assertEqual(listed, {str(session.pk): digits, str(other.pk): '923001234567'})
        self.stop()
        event, listed = streamed()
        self.assertEqual((event['phone'], event['message']['text']['body']), (None, 'hi'))
        self.assertNotIn(digits, json.dumps(event))
        self.assertEqual(listed, {str(session.pk): None, str(other.pk): '923001234567'})
        E.set_sharing(self.enquiry, E.USER, True)
        self.assertEqual(streamed()[0]['phone'], digits)
        self.assertEqual(privacy.hidden_session_phones([digits], None), set())     # no organization: nothing to hide

    def test_list_does_not_query_per_lead_for_consent(self):
        Lead.objects.create(user=make_client(), organization=self.org)
        with CaptureQueriesContext(connection, ) as q:
            self.get('')
        self.assertLessEqual(sum('enquiries' in x['sql'] for x in q.captured_queries), 2)
