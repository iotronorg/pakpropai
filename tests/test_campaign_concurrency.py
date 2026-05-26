"""
Scale and concurrency conformance tests for the campaign dispatch pipeline.
Verifies correct behaviour with 5,000 leads.
"""
import threading
from unittest.mock import MagicMock, patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignRecipient
from apps.leads.models import Lead
from tests.factories import make_developer

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_N = 5_000
_N_THREAD = 50  # smaller pool for threading test


def _bulk_leads(org, n: int):
    """Create n User+Lead pairs for org via bulk_create."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    users = User.objects.bulk_create([
        User(phone=f'+1{100_000_000 + n * 10 + i}', role='client')
        for i in range(n)
    ])
    Lead.objects.bulk_create([
        Lead(user=u, organization=org, status='warm')
        for u in users
    ])


def _mark_sent(recipient, wa_client, limiter):
    """Mock dispatch_one side-effect that marks a recipient as SENT."""
    recipient.delivery_status = CampaignRecipient.DeliveryStatus.SENT
    recipient.sent_at = timezone.now()
    recipient.save(update_fields=['delivery_status', 'sent_at'])


@override_settings(CACHES=_LOCMEM, CELERY_TASK_ALWAYS_EAGER=True)
class CampaignFiveThousandLeadsTest(TestCase):
    """build_recipients and full dispatch handle 5,000 leads without data loss."""

    @classmethod
    def setUpTestData(cls):
        cls.dev_user, cls.org = make_developer()
        _bulk_leads(cls.org, _N)

    def setUp(self):
        self.campaign = Campaign.objects.create(
            organization=self.org,
            created_by=self.dev_user,
            name="5k Scale Test",
            message_template="Hello",
            status=Campaign.Status.SENDING,
        )

    def test_build_recipients_creates_exactly_n_rows(self):
        """build_recipients must create one row per lead."""
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        count = CampaignOrchestrator().build_recipients(self.campaign)
        self.assertEqual(count, _N)
        self.assertEqual(
            CampaignRecipient.objects.filter(campaign=self.campaign).count(),
            _N,
        )

    def test_build_recipients_is_idempotent(self):
        """Calling build_recipients twice must not create duplicate rows (ignore_conflicts)."""
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        orch = CampaignOrchestrator()
        orch.build_recipients(self.campaign)
        orch.build_recipients(self.campaign)
        self.assertEqual(
            CampaignRecipient.objects.filter(campaign=self.campaign).count(),
            _N,
        )

    @patch('apps.campaigns.campaign_manager.CampaignOrchestrator.dispatch_one')
    @patch('apps.whatsapp.client.get_wa_client')
    def test_dispatch_calls_dispatch_one_for_every_recipient(self, mock_factory, mock_dispatch):
        """dispatch_campaign_to_recipients must invoke dispatch_one once per recipient."""
        mock_factory.return_value = MagicMock()
        mock_dispatch.side_effect = _mark_sent

        from apps.campaigns.tasks import dispatch_campaign_to_recipients
        dispatch_campaign_to_recipients(str(self.campaign.id))

        self.assertEqual(mock_dispatch.call_count, _N)

    @patch('apps.campaigns.campaign_manager.CampaignOrchestrator.dispatch_one')
    @patch('apps.whatsapp.client.get_wa_client')
    def test_dispatch_marks_campaign_sent_after_all_recipients(self, mock_factory, mock_dispatch):
        """Campaign must be SENT with correct sent_count after all dispatches succeed."""
        mock_factory.return_value = MagicMock()
        mock_dispatch.side_effect = _mark_sent

        from apps.campaigns.tasks import dispatch_campaign_to_recipients
        dispatch_campaign_to_recipients(str(self.campaign.id))

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.SENT)
        self.assertEqual(self.campaign.sent_count, _N)
        self.assertEqual(self.campaign.failed_count, 0)

    def test_get_progress_totals_for_n_pending_recipients(self):
        """get_progress must return total=N, pending=N, pct_complete=0.0 for fresh recipients."""
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        CampaignOrchestrator().build_recipients(self.campaign)

        progress = CampaignOrchestrator().get_progress(str(self.campaign.id))
        self.assertEqual(progress['total'], _N)
        self.assertEqual(progress['pending'], _N)
        self.assertEqual(progress['sent'], 0)
        self.assertEqual(progress['pct_complete'], 0.0)


@override_settings(CACHES=_LOCMEM)
class CampaignBuildRecipientsRaceTest(TransactionTestCase):
    """Concurrent build_recipients calls must not create duplicate recipient rows."""

    def setUp(self):
        self.dev_user, self.org = make_developer()
        _bulk_leads(self.org, _N_THREAD)
        self.campaign = Campaign.objects.create(
            organization=self.org,
            created_by=self.dev_user,
            name="Race Build Test",
            message_template="Hi",
            status=Campaign.Status.SENDING,
        )

    def test_concurrent_build_recipients_no_duplicates(self):
        """Two concurrent build_recipients calls must not create more than N rows."""
        from apps.campaigns.campaign_manager import CampaignOrchestrator
        results = []

        def build():
            results.append(CampaignOrchestrator().build_recipients(self.campaign))

        t1 = threading.Thread(target=build)
        t2 = threading.Thread(target=build)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        actual = CampaignRecipient.objects.filter(campaign=self.campaign).count()
        self.assertEqual(actual, _N_THREAD)  # ignore_conflicts prevents duplicates
