from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch, MagicMock

from tests.factories import make_developer, make_client, make_lead


class TestReEngagementScanner(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()
        self.dev_b, self.org_b = make_developer(org_name='Org B')

    def _cold_qualified_lead(self, org=None, hours_ago=80, follow_up_sent_at=None):
        u = make_client()
        return make_lead(
            u, org or self.org,
            status='qualified',
            last_contacted_at=timezone.now() - timedelta(hours=hours_ago),
            follow_up_sent_at=follow_up_sent_at,
        )

    @patch('apps.config.services.SystemConfigService.get', return_value='true')
    def test_finds_qualified_leads_older_than_72h(self, _mock):
        from apps.campaigns.re_engagement import ReEngagementScanner
        lead = self._cold_qualified_lead(hours_ago=80)
        qs = ReEngagementScanner().find_cold_leads()
        self.assertIn(lead, qs)

    @patch('apps.config.services.SystemConfigService.get', return_value='true')
    def test_excludes_leads_contacted_within_72h(self, _mock):
        from apps.campaigns.re_engagement import ReEngagementScanner
        lead = self._cold_qualified_lead(hours_ago=50)
        qs = ReEngagementScanner().find_cold_leads()
        self.assertNotIn(lead, qs)

    @patch('apps.config.services.SystemConfigService.get', return_value='true')
    def test_excludes_leads_with_recent_follow_up(self, _mock):
        from apps.campaigns.re_engagement import ReEngagementScanner
        recent_followup = timezone.now() - timedelta(days=1)
        lead = self._cold_qualified_lead(hours_ago=80, follow_up_sent_at=recent_followup)
        qs = ReEngagementScanner().find_cold_leads()
        self.assertNotIn(lead, qs)

    @patch('apps.config.services.SystemConfigService.get', return_value='true')
    def test_org_scoping(self, _mock):
        from apps.campaigns.re_engagement import ReEngagementScanner
        lead_a = self._cold_qualified_lead(org=self.org,   hours_ago=80)
        lead_b = self._cold_qualified_lead(org=self.org_b, hours_ago=80)
        qs = ReEngagementScanner().find_cold_leads(org=self.org)
        self.assertIn(lead_a, qs)
        self.assertNotIn(lead_b, qs)

    @patch('apps.config.services.SystemConfigService.get', return_value='false')
    def test_returns_empty_when_feature_flag_off(self, _mock):
        from apps.campaigns.re_engagement import ReEngagementScanner
        self._cold_qualified_lead(hours_ago=80)
        qs = ReEngagementScanner().find_cold_leads()
        self.assertEqual(list(qs), [])


class TestLeadContextBuilder(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()
        self.client_user = make_client(phone='+923001234567')
        self.lead = make_lead(self.client_user, self.org, status='qualified',
                              last_contacted_at=timezone.now() - timedelta(days=4))

    def test_transcript_includes_wa_messages(self):
        from apps.campaigns.re_engagement import LeadContextBuilder
        from apps.whatsapp.models import WhatsAppSession, WhatsAppMessage
        session = WhatsAppSession.objects.create(
            phone='923001234567', state='ai_queue', organization=self.org)
        WhatsAppMessage.objects.create(
            session=session, direction='inbound', msg_type='text', body='I want a 3BR',
            wa_message_id='test_msg_1')
        WhatsAppMessage.objects.create(
            session=session, direction='outbound', msg_type='text', body='Great, here are options',
            wa_message_id='test_msg_2')
        ctx = LeadContextBuilder().build_context(self.lead)
        self.assertIn('I want a 3BR', ctx.transcript)
        self.assertIn('Great, here are options', ctx.transcript)

    def test_handles_lead_with_no_wa_session(self):
        from apps.campaigns.re_engagement import LeadContextBuilder
        ctx = LeadContextBuilder().build_context(self.lead)
        self.assertEqual(ctx.transcript, '')

    def test_crm_timeline_includes_lead_activities(self):
        from apps.campaigns.re_engagement import LeadContextBuilder
        from apps.leads.models import LeadActivity
        LeadActivity.objects.create(
            lead=self.lead, actor=None,
            action=LeadActivity.ActionType.NOTE,
            notes='Interested in DHA Phase 6',
        )
        ctx = LeadContextBuilder().build_context(self.lead)
        self.assertIn('Interested in DHA Phase 6', ctx.crm_timeline)

    def test_handles_lead_with_no_activity(self):
        from apps.campaigns.re_engagement import LeadContextBuilder
        ctx = LeadContextBuilder().build_context(self.lead)
        self.assertEqual(ctx.crm_timeline, '')


class TestReEngagementPromptGenerator(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()
        self.client_user = make_client(phone='+923009876543')
        self.lead = make_lead(
            self.client_user, self.org,
            status='qualified',
            last_contacted_at=timezone.now() - timedelta(days=4),
            intent='buy',
            city_interest='DHA Lahore',
        )

    def _make_context(self):
        from apps.campaigns.re_engagement import LeadContext
        return LeadContext(
            transcript='[inbound] I want a 3BR villa\n[outbound] Great, found 4 options',
            crm_timeline='[note] Interested in DHA Phase 6 (2026-05-22)',
            lead_summary={'intent': 'buy', 'budget_min': 10000000,
                          'budget_max': 20000000, 'city': 'DHA Lahore', 'score': 75},
        )

    @patch('apps.campaigns.re_engagement.ReEngagementPromptGenerator._call_llm')
    def test_generated_message_within_300_chars(self, mock_call):
        from apps.campaigns.re_engagement import ReEngagementPromptGenerator
        mock_call.return_value = 'Hi! We have new DHA Lahore listings matching your search.'
        gen = ReEngagementPromptGenerator()
        msg = gen.generate(self.lead, self._make_context(), self.org)
        self.assertLessEqual(len(msg), 300)

    @patch('apps.campaigns.re_engagement.ReEngagementPromptGenerator._call_llm')
    def test_falls_back_to_canned_message_on_empty_llm(self, mock_call):
        from apps.campaigns.re_engagement import ReEngagementPromptGenerator, _CANNED_MESSAGE
        mock_call.return_value = ''
        gen = ReEngagementPromptGenerator()
        msg = gen.generate(self.lead, self._make_context(), self.org)
        self.assertEqual(msg, _CANNED_MESSAGE)

    @patch('apps.campaigns.re_engagement.ReEngagementPromptGenerator._call_llm')
    def test_falls_back_on_llm_exception(self, mock_call):
        from apps.campaigns.re_engagement import ReEngagementPromptGenerator, _CANNED_MESSAGE
        mock_call.side_effect = Exception("LLM timeout")
        gen = ReEngagementPromptGenerator()
        msg = gen.generate(self.lead, self._make_context(), self.org)
        self.assertEqual(msg, _CANNED_MESSAGE)

    @patch('apps.campaigns.re_engagement.ReEngagementPromptGenerator._call_llm')
    def test_strips_llm_preamble(self, mock_call):
        from apps.campaigns.re_engagement import ReEngagementPromptGenerator
        mock_call.return_value = "Sure! Here's a message:\nWe have new listings for you."
        gen = ReEngagementPromptGenerator()
        msg = gen.generate(self.lead, self._make_context(), self.org)
        self.assertNotIn("Sure!", msg)
