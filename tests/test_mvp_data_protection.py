"""MVP acceptance 10 (nothing secret in plain text) and 11 (export and deletion cover everything)."""
import io
import re
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import connection, models
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.ai.models import PendingAction
from apps.compliance import erasure as E
from apps.compliance import tasks as T
from apps.compliance.aml_gateway import AMLScreeningGateway
from apps.compliance.models import ConsentRecord, DataDeletionRequest, DataExportRequest, PrivacyAuditLog
from apps.conversations import store
from apps.conversations.models import Conversation
from apps.core.crypto import EncryptedJSONField, EncryptedTextField
from apps.leads.models import ConversationMessage, Lead, LeadActivity
from apps.notifications.models import Notification
from apps.organizations.models import OrganizationMembership
from apps.preferences.models import SavedSearch
from apps.properties.models import Property
from apps.reports.models import Report
from apps.users.models import User
from apps.verification.models import DocumentScan, FraudBlacklist
from apps.verification.services import FraudCheckService
from apps.voice.models import VoiceCallSession
from apps.whatsapp.models import WhatsAppMessage, WhatsAppSession
from tests.factories import make_agent, make_client, make_developer, make_lead, make_org, make_property, make_user
from tests.test_runtime import LOCMEM

CNIC = '35202-1234567-1'
SENSITIVE = re.compile(r'secret|token|password|api_key|cnic|national|passport|id_number|otp|credential|private_key|'
                       r'tax_id|ntn|iban|raw_ocr|raw_docs|ocr_data|extracted_fields|emirates|_key$|signature')
# Plain columns whose name looks sensitive, and why they may be plain.
PLAIN = {
    'users.User.password': 'Django password hash',
    'organizations.OrgPaymentSettings.safepay_merchant_key': 'public merchant id',
    'compliance.ComplianceSanctionRecord.id_number_prefix': 'first four digits',
    'compliance.ComplianceSanctionRecord.id_number_hash': 'keyed hash',
    'compliance.SanctionScreeningResult.id_number_prefix': 'first four digits',
    'compliance.SanctionScreeningResult.id_number_hash': 'keyed hash',
    'verification.FraudBlacklist.token_index': 'keyed hash',
    'whatsapp.OrgWhatsAppConfig.verify_token_hash': 'keyed hash',
    'whatsapp.OrgWhatsAppConfig.otp_template_name': 'template name',
    'token_blacklist.OutstandingToken.token': 'always blank (KI-102)',
    'payments.Payment.checkout_token': 'short-lived checkout session id',
    'escrow.EscrowDeal.seller_confirmation_token': 'deal lock is off in the MVP (KI-104)',
    'core.OutboxMessage.idempotency_key': 'internal id',
    'ai.PendingAction.conversation_key': 'conversation id',
    'locations.Location.match_key': 'normalised place name',
    'sessions.Session.session_key': 'Django session id (admin only, expires)',
    'django_celery_beat.PeriodicTask.routing_key': 'queue name',
    'audit.AuditBenchmark.location_key': 'place name',
}


class SchemaAuditTest(TestCase):
    def test_sensitive_columns_are_encrypted_or_listed(self):
        plain = set()
        for m in apps.get_models():
            for f in m._meta.local_concrete_fields:
                if isinstance(f, (models.CharField, models.TextField, models.JSONField)) and SENSITIVE.search(f.name) \
                        and not isinstance(f, (EncryptedTextField, EncryptedJSONField)):
                    plain.add(f'{m._meta.label}.{f.name}')
        self.assertEqual(plain - set(PLAIN), set())
        self.assertEqual(set(PLAIN) - plain, set())


@override_settings(CACHES=LOCMEM)
class AtRestTest(TestCase):
    def setUp(self):
        cache.clear()

    def raw(self, table, column):
        with connection.cursor() as cur:
            cur.execute(f'SELECT {column}::text FROM {table}')
            return [r[0] for r in cur.fetchall()]

    def test_json_round_trip_and_api(self):
        u = make_client()
        p = make_property(owner=u)
        p.raw_docs = {'cnic': CNIC, 'n': [1, None]}
        p.save()
        self.assertTrue(all(v.startswith('enc1:') for v in self.raw('properties', 'raw_docs')))
        p.refresh_from_db()
        self.assertEqual(p.raw_docs, {'cnic': CNIC, 'n': [1, None]})
        c = APIClient()
        c.force_authenticate(u)
        self.assertEqual(c.get(f'/api/v1/properties/{p.pk}/').data['raw_docs'], p.raw_docs)
        self.assertEqual(c.patch(f'/api/v1/properties/{p.pk}/', {'raw_docs': {'a': 1}}, format='json').status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.raw_docs, {'a': 1})
        DocumentScan.objects.create(extracted_fields={'cnic': CNIC}, whatsapp_summary=CNIC)
        self.assertEqual(DocumentScan.objects.get().extracted_fields, {'cnic': CNIC})

    def test_blacklist(self):
        admin = make_user(role='admin')
        c = APIClient()
        c.force_authenticate(admin)
        url = '/api/v1/verification/fraud/blacklist/'
        self.assertEqual(c.post(url, {'token': CNIC}, format='json').status_code, 201)
        self.assertEqual(c.post(url, {'token': CNIC.upper(), 'reason': 'x'}, format='json').status_code, 200)
        self.assertEqual(c.get(url).data['results'][0]['token'], CNIC)
        self.assertNotIn(CNIC, str(self.raw('fraud_blacklist', 'token')))
        self.assertTrue(FraudCheckService.is_blacklisted(CNIC))
        self.assertEqual(FraudCheckService.check(f'Is {CNIC} safe?')['source'], 'blacklist')
        self.assertIsNone(cache.get(f'fraud:blacklist:{CNIC}'))
        FraudBlacklist.objects.create(token='Fake Society')
        self.assertTrue(FraudCheckService.is_blacklisted('fake society'))
        FraudBlacklist.objects.exclude(token_index=FraudBlacklist.index(CNIC)).get().delete()
        FraudBlacklist.objects.get().delete()
        self.assertFalse(FraudCheckService.is_blacklisted(CNIC))

    def test_fraud_report_works(self):
        u = make_client()
        p = make_property(owner=u)
        FraudBlacklist.objects.create(token=u.phone)
        from apps.reports.tasks import _content_fraud_check
        self.assertTrue(_content_fraud_check(Report(user=u, property=p, report_type='fraud_check'))['owner_blacklisted'])

    def test_refresh_tokens_are_not_stored_and_still_revocable(self):
        u = make_client()
        token = RefreshToken.for_user(u)
        self.assertEqual(self.raw('token_blacklist_outstandingtoken', 'token'), [''])
        token.blacklist()
        from rest_framework_simplejwt.exceptions import TokenError
        with self.assertRaises(TokenError):
            RefreshToken(str(token))

    def test_id_hash_is_keyed(self):
        import hashlib
        self.assertNotEqual(AMLScreeningGateway._hash_id(CNIC), hashlib.sha256(CNIC.encode()).hexdigest())

    def test_scan(self):
        out = io.StringIO()
        call_command('scan_plaintext', stdout=out)
        self.assertIn('No plain-text', out.getvalue())
        Notification.objects.create(user=make_client(), message=f'id {CNIC}')
        u = make_client()
        with connection.cursor() as cur:
            cur.execute('UPDATE users SET cnic = %s WHERE id = %s', ['x', str(u.pk)])
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command('scan_plaintext', stdout=out)
        self.assertIn('notifications.Notification.message: cnic: 1', out.getvalue())
        self.assertIn('users.User.cnic: not encrypted: 1', out.getvalue())
        self.assertNotIn(CNIC, out.getvalue())


# Every relation to a person (or to a lead): what erasure does with it. A new one must be added here on purpose.
ERASED = 'erased'            # row deleted, emptied or unlinked by a step
KEPT = 'kept, the account it points to is anonymous'
RELATIONS = {
    'admin.LogEntry.user': ERASED, 'token_blacklist.OutstandingToken.user': ERASED, 'users.MFADevice.user': ERASED,
    'users.MFARecoveryCode.user': ERASED, 'organizations.OrganizationMembership.user': ERASED,
    'properties.Property.owner': ERASED, 'ai.AIInteraction.user': ERASED, 'ai.PendingAction.user': ERASED,
    'ai.AITrace.user': ERASED, 'conversations.Conversation.user': ERASED, 'notifications.Notification.user': ERASED,
    'whatsapp.WhatsAppSession.user': ERASED, 'verification.DocumentScan.user': ERASED, 'leads.Lead.user': ERASED,
    'leads.ConversationMessage.sender': ERASED, 'enquiries.Enquiry.user': ERASED,
    'enquiries.ThreadReport.reporter': ERASED, 'viewings.ViewingFeedback.author': ERASED,
    'preferences.Favourite.user': ERASED, 'preferences.SavedSearch.user': ERASED,
    'preferences.PreferenceItem.user': ERASED, 'moderation.UserReport.reporter': ERASED, 'reports.Report.user': ERASED,
    'audit.PropertyAudit.user': ERASED, 'agents.Agent.user': ERASED,
    'leads.LeadActivity.lead': ERASED, 'leads.Appointment.lead': ERASED, 'leads.ConversationMessage.lead': ERASED,
    'enquiries.Enquiry.lead': ERASED, 'campaigns.CampaignRecipient.lead': ERASED, 'voice.VoiceCallSession.lead': ERASED,
    'core.AuditLog.actor': KEPT, 'sysconfig.SystemConfig.updated_by': KEPT, 'organizations.Organization.admin_user': KEPT,
    'organizations.OrganizationConfig.updated_by': KEPT, 'organizations.OrganizationInvite.invitee': KEPT,
    'properties.PropertyImage.uploaded_by': KEPT, 'payments.Payment.user': KEPT, 'escrow.EscrowDeal.buyer': KEPT,
    'escrow.EscrowDeal.seller': KEPT, 'notifications.UserNotificationPreference.user': KEPT,
    'verification.Verification.reviewer': KEPT, 'verification.Verification.requested_by': KEPT,
    'verification.FraudBlacklist.added_by': KEPT, 'leads.LeadActivity.actor': KEPT,
    'leads.Appointment.created_by': KEPT, 'leads.LeadScoreHistory.changed_by': KEPT, 'enquiries.Enquiry.responder': KEPT,
    'audit.AuditBenchmark.updated_by': KEPT, 'agents.Agent.verified_by': KEPT,
    'agents.FreelanceAgentProfile.user': KEPT, 'campaigns.Campaign.created_by': KEPT,
    'compliance.ConsentRecord.user': KEPT, 'compliance.DataExportRequest.user': KEPT,
    'compliance.DataDeletionRequest.user': KEPT, 'compliance.DataDeletionRequest.requested_by': KEPT,
    'compliance.ComplianceSanctionRecord.added_by': KEPT, 'compliance.PrivacyAuditLog.actor': KEPT,
    'inventory.SyncConflictAlert.resolved_by': KEPT, 'voice.VoiceCallSession.barge_in_agent': KEPT,
    'marketplace.BrokerNetworkPartnership.broker_agent': KEPT,
    'marketplace.SyndicationLeadSubmission.submitted_by_agent': KEPT,
    'marketplace.SyndicationLeadSubmission.reviewed_by': KEPT, 'marketplace.CommissionLedgerEntry.broker_agent': KEPT,
    'leads.LeadScoreHistory.lead': KEPT, 'campaigns.ReferralLink.lead': KEPT,
    'marketplace.SyndicationLeadSubmission.lead': KEPT,
}


@override_settings(CACHES=LOCMEM)
class PersonTest(TestCase):
    """One person with data everywhere, all of it holding the same words."""
    WORDS = 'my very private wish'

    def setUp(self):
        cache.clear()
        self.org = make_org(name='Agency', operational_mode='production')
        self.user = make_client(name='Amna Private', email='amna@mail.example', cnic=CNIC, ntn='1234567-8')
        self.phone = self.user.phone

    def fill(self):
        u, w, phone = self.user, self.WORDS, self.phone.lstrip('+')
        self.lead = make_lead(u, org=self.org, notes=w, city_interest='Lahore', intent_signals={'x': w})
        LeadActivity.objects.create(lead=self.lead, action='note', notes=w, meta={'m': w})
        ConversationMessage.objects.create(lead=self.lead, direction='inbound', body=w)
        s = WhatsAppSession.objects.create(phone=phone, organization=self.org, context={'m': w})     # not linked yet
        WhatsAppMessage.objects.create(session=s, wa_message_id='m1', direction='inbound', body=w)
        store.add_exchange(phone, None, u, w, 'noted')
        PendingAction.objects.create(conversation_key='k', user=u, tool='t', side_effect='low', args='{}', args_hash='',
                                     prompt=w, expires_at=timezone.now())
        SavedSearch.objects.create(user=u, name=w, criteria={}, consented_at=timezone.now(), since=timezone.now(),
                                   next_run_at=timezone.now())
        Notification.objects.create(user=u, message=w)
        Report.objects.create(user=u, report_type='fraud_check', content={'x': w})
        DocumentScan.objects.create(user=u, owner_name=w, cnic_number=CNIC)
        VoiceCallSession.objects.create(lead=self.lead, call_sid='c1', from_phone=self.phone, to_phone='+1',
                                        direction='inbound', transcript=w)
        ConsentRecord.objects.create(user=u, purpose='marketing')
        self.listing = make_property(owner=u, title=w, description=w)
        return self.lead

    def plain_dump(self):
        """Every stored value that still mentions the person (encrypted ones decrypted)."""
        hits = []
        for m in apps.get_models():
            if m._meta.proxy or not m._meta.managed:
                continue
            for row in m.objects.all() if hasattr(m, 'objects') else ():
                text = ' '.join(str(getattr(row, f.attname)) for f in m._meta.local_concrete_fields)
                if any(x in text for x in (self.WORDS, self.phone.lstrip('+'), 'Amna', 'amna@', CNIC, '1234567-8')):
                    hits.append(m._meta.label)
        return sorted(set(hits))

    def run_erasure(self):
        req = DataDeletionRequest.objects.create(user=self.user)
        T.execute_data_deletion(str(req.pk))
        req.refresh_from_db()
        self.user.refresh_from_db()
        return req


class ErasureTest(PersonTest):
    def test_every_relation_is_decided(self):
        now = {f'{r.related_model._meta.label}.{r.field.name}' for M in (User, Lead) for r in M._meta.related_objects}
        self.assertEqual(now, set(RELATIONS))

    def test_nothing_about_the_person_is_left(self):
        self.fill()
        self.assertGreater(len(self.plain_dump()), 10)
        req = self.run_erasure()
        self.assertEqual((req.status, 'errors' in req.erasure_scope), ('completed', False))
        self.assertEqual(self.plain_dump(), [])
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())
        self.listing.refresh_from_db()
        self.assertEqual((self.listing.status, self.listing.is_active), ('archived', False))
        self.assertEqual(PrivacyAuditLog.objects.get(action='erasure_completed').subject_identifier, E._sha256(self.phone))
        make_client(phone=self.phone)                                   # may register again

    def test_agent(self):
        user, agent = make_agent(org=self.org)
        phone = user.phone
        type(agent).objects.filter(pk=agent.pk).update(name='Amna Agent', national_id_number=CNIC, bio=self.WORDS)
        req = DataDeletionRequest.objects.create(user=user)
        T.execute_data_deletion(str(req.pk))
        agent.refresh_from_db()
        self.assertEqual((agent.name, agent.national_id_number, agent.bio), ('[DELETED]', '', ''))
        self.assertNotEqual(agent.phone, phone)
        self.assertFalse(OrganizationMembership.objects.filter(user=user).exists())

    def test_a_failed_step_is_not_called_complete_and_is_repeated(self):
        self.fill()
        with patch('apps.voice.models.VoiceCallSession.objects.filter', side_effect=OSError(self.WORDS)):
            req = self.run_erasure()
        self.assertEqual((req.status, req.completed_at), ('failed', None))
        self.assertEqual(req.erasure_scope['errors'], {'voice_calls_emptied': 'OSError'})
        self.assertEqual((self.user.phone, self.user.is_active), (self.phone, False))
        DataDeletionRequest.objects.update(requested_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(T.process_data_requests(), 1)
        req.refresh_from_db()
        self.assertEqual(req.status, 'completed')
        self.assertEqual(self.plain_dump(), [])

    def test_org_task_retries_an_incomplete_erasure(self):
        req = DataDeletionRequest.objects.create(user=self.user)
        with patch('apps.reports.models.Report.objects.filter', side_effect=OSError):
            with self.assertRaises(Exception):
                T.execute_rtbf_erasure.apply(args=[str(req.pk)], throw=True)
        req.refresh_from_db()
        self.assertEqual(req.status, 'failed')

    def test_overdue_is_reported(self):
        for days, level in ((1, None), (25, 'ERROR')):
            DataDeletionRequest.objects.all().delete()
            r = DataDeletionRequest.objects.create(user=self.user)
            DataDeletionRequest.objects.filter(pk=r.pk).update(requested_at=timezone.now() - timedelta(days=days))
            e = DataExportRequest.objects.create(user=self.user)
            with patch.object(T.execute_data_deletion, 'delay') as d, patch.object(T.generate_data_export, 'delay') as x:
                if level:
                    with self.assertLogs('apps.compliance.tasks', level):
                        T.process_data_requests()
                else:
                    with self.assertNoLogs('apps.compliance.tasks', 'ERROR'):
                        T.process_data_requests()
            d.assert_called_once()
            x.assert_not_called()                                       # asked less than an hour ago
            e.delete()

    def test_who_cannot_be_erased_now(self):
        dev, org = make_developer()
        self.assertIn('ownership', E.blocked_reason(dev))
        self.assertIn('Staff', E.blocked_reason(make_user(role='admin')))
        self.assertEqual(E.blocked_reason(self.user), '')
        c = APIClient()
        c.force_authenticate(dev)
        with patch('apps.compliance.views._require_gdpr'):
            self.assertEqual(c.delete('/api/v1/compliance/my-data/').status_code, 409)
        self.assertFalse(DataDeletionRequest.objects.exists())


class OrganizationRequestTest(PersonTest):
    def ask(self, user, phone):
        c = APIClient()
        c.force_authenticate(user)
        with patch('apps.compliance.tasks.execute_rtbf_erasure.delay'):
            return c.post('/api/v1/compliance/rtbf/initiate/', {'phone_e164': phone}, format='json')

    def test_an_organization_erases_only_its_own_customer(self):
        dev, org = make_developer()
        unknown = self.ask(dev, '+923009999999')
        refused = self.ask(dev, self.phone)
        self.assertEqual((refused.status_code, refused.data['detail']), (400, unknown.data['detail']))
        make_lead(self.user, org=org)
        self.assertEqual(self.ask(dev, self.phone).status_code, 202)
        for extra in (lambda: make_lead(self.user, org=self.org), lambda: make_property(owner=self.user),
                      lambda: Conversation.objects.create(user=self.user, key='global:1', last_message_at=timezone.now())):
            row = extra()
            self.assertEqual(self.ask(dev, self.phone).status_code, 400, row)
            row.delete()
        self.assertEqual(self.ask(dev, dev.phone).status_code, 400)
        self.assertEqual(self.ask(make_user(role='admin'), self.phone).status_code, 202)


class ExportTest(PersonTest):
    def test_export_has_every_kind_of_data(self):
        import json
        self.fill()
        req = DataExportRequest.objects.create(user=self.user)
        T.generate_data_export(str(req.pk))
        data = json.loads(req.file.payload)
        for key in ('leads', 'lead_history', 'agency_messages', 'whatsapp_messages', 'assistant_messages',
                    'confirmations', 'saved_searches', 'notifications', 'reports', 'documents', 'voice_calls', 'listings'):
            self.assertIn(self.WORDS, json.dumps(data[key]), key)
        for key in ('enquiries', 'thread_messages', 'preferences', 'favourites', 'viewings', 'consents', 'payments',
                    'reservations', 'memberships', 'agent_profile', 'viewing_feedback'):
            self.assertIn(key, data)
        self.assertEqual((data['user']['cnic'], data['documents'][0]['cnic_number']), (CNIC, CNIC))

    def test_nothing_of_another_person(self):
        import json
        self.fill()
        req = DataExportRequest.objects.create(user=make_client())
        T.generate_data_export(str(req.pk))
        self.assertNotIn(self.WORDS, req.file.payload)
        self.assertTrue(json.loads(req.file.payload)['user'])
