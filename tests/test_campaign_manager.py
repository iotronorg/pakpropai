import time
import threading
import uuid
import json
from unittest.mock import patch, MagicMock
from django.test import TestCase

from apps.campaigns.campaign_manager import MetaTierRateLimiter, TIER_LIMITS


class TestMetaTierRateLimiter(TestCase):

    def setUp(self):
        self.org_id = str(uuid.uuid4())
        self.limiter = MetaTierRateLimiter()
        self.limiter._redis.delete(self.limiter._key(self.org_id))

    def tearDown(self):
        self.limiter._redis.delete(self.limiter._key(self.org_id))

    def test_fresh_bucket_allows_first_acquire(self):
        result = self.limiter.acquire(self.org_id, tier=1)
        self.assertTrue(result)

    def test_exhausted_bucket_rejects(self):
        for _ in range(10):
            self.limiter.acquire(self.org_id, tier=1)
        result = self.limiter.acquire(self.org_id, tier=1)
        self.assertFalse(result)

    def test_tier3_higher_rate_than_tier1(self):
        org_t1 = str(uuid.uuid4())
        org_t3 = str(uuid.uuid4())
        for _ in range(10):
            self.limiter.acquire(org_t1, tier=1)
            self.limiter.acquire(org_t3, tier=3)
        time.sleep(0.05)
        result_t1 = self.limiter.acquire(org_t1, tier=1)
        result_t3 = self.limiter.acquire(org_t3, tier=3)
        self.assertFalse(result_t1)
        self.assertTrue(result_t3)

    def test_different_orgs_have_independent_buckets(self):
        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        for _ in range(10):
            self.limiter.acquire(org_a, tier=1)
        self.assertFalse(self.limiter.acquire(org_a, tier=1))
        self.assertTrue(self.limiter.acquire(org_b, tier=1))

    def test_no_double_spend_under_concurrency(self):
        """Ten concurrent threads all call acquire(); only burst_capacity succeed."""
        results = []
        lock = threading.Lock()

        def call():
            r = self.limiter.acquire(self.org_id, tier=1)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=call) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertLessEqual(results.count(True), 10)

    def test_backoff_is_exponential(self):
        b0 = self.limiter.get_backoff_seconds(0)
        b1 = self.limiter.get_backoff_seconds(1)
        b2 = self.limiter.get_backoff_seconds(2)
        self.assertLess(b0, b1)
        self.assertLess(b1, b2)

    def test_backoff_caps_at_300(self):
        b = self.limiter.get_backoff_seconds(20)
        self.assertLessEqual(b, 300)

    def test_backoff_has_jitter(self):
        values = {self.limiter.get_backoff_seconds(3) for _ in range(20)}
        self.assertGreater(len(values), 1)


class TestMetaTemplateFetcher(TestCase):

    def setUp(self):
        from tests.factories import make_org
        from apps.whatsapp.models import OrgWhatsAppConfig
        self.org = make_org()
        OrgWhatsAppConfig.objects.create(
            organization=self.org,
            waba_id='1234567890',
            access_token='test_token',
            is_active=True,
        )

    def _mock_meta_response(self):
        return {
            "data": [
                {
                    "name": "property_promo",
                    "language": "en_US",
                    "status": "APPROVED",
                    "category": "MARKETING",
                    "components": [
                        {"type": "BODY", "text": "Check out {{1}} in {{2}}!"}
                    ],
                },
                {
                    "name": "pending_template",
                    "language": "en_US",
                    "status": "PENDING",
                    "category": "MARKETING",
                    "components": [],
                },
                {
                    "name": "re_engage",
                    "language": "ur",
                    "status": "APPROVED",
                    "category": "UTILITY",
                    "components": [
                        {"type": "BODY", "text": "سلام! کیا آپ ابھی بھی دلچسپی رکھتے ہیں؟"}
                    ],
                },
            ]
        }

    @patch('apps.campaigns.campaign_manager.requests.get')
    def test_returns_only_approved_templates(self, mock_get):
        from apps.campaigns.campaign_manager import MetaTemplateFetcher
        from django.core.cache import cache
        cache.delete(f"wa_templates:{self.org.id}")
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: self._mock_meta_response(),
        )
        fetcher = MetaTemplateFetcher()
        templates = fetcher.list_templates(self.org)
        names = [t.name for t in templates]
        self.assertIn('property_promo', names)
        self.assertIn('re_engage', names)
        self.assertNotIn('pending_template', names)

    @patch('apps.campaigns.campaign_manager.requests.get')
    def test_result_is_cached_on_second_call(self, mock_get):
        from apps.campaigns.campaign_manager import MetaTemplateFetcher
        from django.core.cache import cache
        cache.delete(f"wa_templates:{self.org.id}")
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: self._mock_meta_response(),
        )
        fetcher = MetaTemplateFetcher()
        fetcher.list_templates(self.org)
        fetcher.list_templates(self.org)
        self.assertEqual(mock_get.call_count, 1)

    @patch('apps.campaigns.campaign_manager.requests.get')
    def test_returns_empty_list_on_api_error(self, mock_get):
        from apps.campaigns.campaign_manager import MetaTemplateFetcher
        from django.core.cache import cache
        cache.delete(f"wa_templates:{self.org.id}")
        mock_get.side_effect = Exception("network error")
        fetcher = MetaTemplateFetcher()
        result = fetcher.list_templates(self.org)
        self.assertEqual(result, [])

    @patch('apps.campaigns.campaign_manager.requests.get')
    def test_get_template_returns_matching_template(self, mock_get):
        from apps.campaigns.campaign_manager import MetaTemplateFetcher
        from django.core.cache import cache
        cache.delete(f"wa_templates:{self.org.id}")
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: self._mock_meta_response(),
        )
        fetcher = MetaTemplateFetcher()
        t = fetcher.get_template(self.org, 're_engage', 'ur')
        self.assertIsNotNone(t)
        self.assertEqual(t.name, 're_engage')

    @patch('apps.campaigns.campaign_manager.requests.get')
    def test_get_template_returns_none_when_not_found(self, mock_get):
        from apps.campaigns.campaign_manager import MetaTemplateFetcher
        from django.core.cache import cache
        cache.delete(f"wa_templates:{self.org.id}")
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: self._mock_meta_response(),
        )
        fetcher = MetaTemplateFetcher()
        t = fetcher.get_template(self.org, 'nonexistent', 'en_US')
        self.assertIsNone(t)


class TestCampaignOrchestrator(TestCase):

    def setUp(self):
        from django.utils import timezone
        from datetime import timedelta
        from tests.factories import make_developer, make_lead, make_client
        self.dev_user, self.org = make_developer()
        self.dev_b, self.org_b = make_developer(org_name='Org B')
        self.leads = []
        for i in range(5):
            u = make_client(phone=f'+9230099{8000+i:05d}')
            lead = make_lead(u, self.org, status='qualified',
                             last_contacted_at=timezone.now() - timedelta(days=1))
            self.leads.append(lead)
        u_b = make_client(phone='+923001999999')
        self.lead_b = make_lead(u_b, self.org_b, status='qualified')

    def _make_campaign(self, **kwargs):
        from apps.campaigns.models import Campaign
        return Campaign.objects.create(
            organization=self.org,
            created_by=self.dev_user,
            name='Test',
            audience_filter='qualified',
            status=Campaign.Status.SENDING,
            **kwargs,
        )

    def test_build_recipients_status_filter(self):
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        from apps.campaigns.models import CampaignRecipient
        campaign = self._make_campaign()
        count = CampaignOrchestrator().build_recipients(campaign)
        self.assertEqual(count, 5)
        self.assertEqual(CampaignRecipient.objects.filter(campaign=campaign).count(), 5)

    def test_build_recipients_is_idempotent(self):
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        from apps.campaigns.models import CampaignRecipient
        campaign = self._make_campaign()
        CampaignOrchestrator().build_recipients(campaign)
        CampaignOrchestrator().build_recipients(campaign)
        self.assertEqual(CampaignRecipient.objects.filter(campaign=campaign).count(), 5)

    def test_build_recipients_budget_filter(self):
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        from apps.campaigns.models import CampaignRecipient
        from tests.factories import make_client, make_lead
        u = make_client(phone='+923009111111')
        rich_lead = make_lead(u, self.org, status='qualified',
                              budget_min=10_000_000, budget_max=20_000_000)
        campaign = self._make_campaign(budget_min=15_000_000)
        CampaignOrchestrator().build_recipients(campaign)
        recipients = CampaignRecipient.objects.filter(campaign=campaign)
        lead_ids = list(recipients.values_list('lead_id', flat=True))
        self.assertIn(rich_lead.id, lead_ids)
        for lead in self.leads:
            self.assertNotIn(lead.id, lead_ids)

    def test_build_recipients_excludes_other_org_leads(self):
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        from apps.campaigns.models import CampaignRecipient, Campaign
        campaign = Campaign.objects.create(
            organization=self.org, created_by=self.dev_user,
            name='Cross-org test', audience_filter='all',
            status=Campaign.Status.SENDING,
        )
        CampaignOrchestrator().build_recipients(campaign)
        lead_ids = list(CampaignRecipient.objects.filter(campaign=campaign)
                        .values_list('lead_id', flat=True))
        self.assertNotIn(self.lead_b.id, lead_ids)

    def test_get_progress_returns_correct_counts(self):
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        from apps.campaigns.models import CampaignRecipient
        campaign = self._make_campaign()
        CampaignOrchestrator().build_recipients(campaign)
        recipients = list(CampaignRecipient.objects.filter(campaign=campaign)[:3])
        recipients[0].delivery_status = 'sent';  recipients[0].save()
        recipients[1].delivery_status = 'sent';  recipients[1].save()
        recipients[2].delivery_status = 'failed'; recipients[2].save()
        snap = CampaignOrchestrator().get_progress(str(campaign.id))
        self.assertEqual(snap['total'],   5)
        self.assertEqual(snap['sent'],    2)
        self.assertEqual(snap['failed'],  1)
        self.assertEqual(snap['pending'], 2)
        self.assertAlmostEqual(snap['pct_complete'], 60.0)

    @patch('apps.campaigns.campaign_manager.requests.get')
    def test_dispatch_one_uses_send_template_when_name_set(self, _mock):
        from apps.campaigns.campaign_manager import CampaignOrchestrator, MetaTierRateLimiter
        from apps.campaigns.models import CampaignRecipient
        from apps.whatsapp.client import WhatsAppClient
        campaign = self._make_campaign(meta_template_name='promo', meta_template_language='en_US')
        CampaignOrchestrator().build_recipients(campaign)
        recipient = CampaignRecipient.objects.filter(campaign=campaign).first()
        limiter = MetaTierRateLimiter()
        wa = MagicMock(spec=WhatsAppClient)
        wa.send_template.return_value = {'messages': [{'id': 'wamid.123'}]}
        CampaignOrchestrator().dispatch_one(recipient, wa, limiter)
        wa.send_template.assert_called_once()
        recipient.refresh_from_db()
        self.assertEqual(recipient.delivery_status, 'sent')
        self.assertEqual(recipient.meta_message_id, 'wamid.123')

    @patch('apps.campaigns.campaign_manager.requests.get')
    def test_dispatch_one_uses_send_text_fallback(self, _mock):
        from apps.campaigns.campaign_manager import CampaignOrchestrator, MetaTierRateLimiter
        from apps.campaigns.models import CampaignRecipient
        from apps.whatsapp.client import WhatsAppClient
        campaign = self._make_campaign(message_template='Hello!')
        CampaignOrchestrator().build_recipients(campaign)
        recipient = CampaignRecipient.objects.filter(campaign=campaign).first()
        limiter = MetaTierRateLimiter()
        wa = MagicMock(spec=WhatsAppClient)
        wa.send_text.return_value = {'messages': [{'id': 'wamid.456'}]}
        CampaignOrchestrator().dispatch_one(recipient, wa, limiter)
        wa.send_text.assert_called_once()
        recipient.refresh_from_db()
        self.assertEqual(recipient.delivery_status, 'sent')
