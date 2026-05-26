"""
Tests for the campaigns feature.
Covers: CRUD API, org scoping, send/schedule/cancel actions, dispatch task.
"""
import threading
from unittest.mock import MagicMock, patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign
from tests.factories import make_developer, make_org, make_membership, make_user

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=_LOCMEM)
class CampaignCRUDTests(TestCase):
    """CRUD endpoints work for a developer with a valid org."""

    def setUp(self):
        self.client = APIClient()
        self.dev_user, self.org = make_developer()
        self.client.force_authenticate(user=self.dev_user)

    def _create(self, name='Test Campaign', msg='Hello!', audience='all'):
        return self.client.post('/api/v1/campaigns/', {
            'name': name,
            'message_template': msg,
            'audience_filter': audience,
        }, format='json')

    def test_create_campaign(self):
        r = self._create()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['name'], 'Test Campaign')
        self.assertEqual(r.data['status'], 'draft')
        self.assertEqual(r.data['audience_filter'], 'all')

    def test_list_campaigns(self):
        self._create('C1')
        self._create('C2')
        r = self.client.get('/api/v1/campaigns/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 2)

    def test_list_filtered_by_status(self):
        self._create('Draft')
        r = self.client.get('/api/v1/campaigns/', {'status': 'scheduled'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 0)

    def test_update_draft_campaign(self):
        r = self._create()
        cid = r.data['id']
        r2 = self.client.patch(f'/api/v1/campaigns/{cid}/', {'name': 'Updated'}, format='json')
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data['name'], 'Updated')

    def test_delete_draft_campaign(self):
        r = self._create()
        cid = r.data['id']
        r2 = self.client.delete(f'/api/v1/campaigns/{cid}/')
        self.assertEqual(r2.status_code, 204)
        self.assertFalse(Campaign.objects.filter(id=cid).exists())

    def test_cannot_delete_sent_campaign(self):
        r = self._create()
        cid = r.data['id']
        Campaign.objects.filter(id=cid).update(status=Campaign.Status.SENT)
        r2 = self.client.delete(f'/api/v1/campaigns/{cid}/')
        self.assertEqual(r2.status_code, 400)


@override_settings(CACHES=_LOCMEM)
class CampaignOrgIsolationTests(TestCase):
    """Org A cannot see or modify Org B's campaigns."""

    def setUp(self):
        self.client_a = APIClient()
        self.dev_a, self.org_a = make_developer(phone='+923001000001', org_name='Org A')
        self.client_a.force_authenticate(user=self.dev_a)

        self.client_b = APIClient()
        self.dev_b, self.org_b = make_developer(phone='+923001000002', org_name='Org B')
        self.client_b.force_authenticate(user=self.dev_b)

        # Create a campaign owned by org_a
        self.campaign_a = Campaign.objects.create(
            organization=self.org_a,
            created_by=self.dev_a,
            name='Org A Campaign',
            message_template='Hello from A',
        )

    def test_org_b_cannot_list_org_a_campaigns(self):
        r = self.client_b.get('/api/v1/campaigns/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 0)

    def test_org_b_cannot_access_org_a_campaign_detail(self):
        r = self.client_b.get(f'/api/v1/campaigns/{self.campaign_a.id}/')
        self.assertEqual(r.status_code, 404)

    def test_unauthenticated_cannot_list(self):
        r = APIClient().get('/api/v1/campaigns/')
        self.assertEqual(r.status_code, 401)


@override_settings(CACHES=_LOCMEM)
class CampaignActionsTests(TestCase):
    """send, schedule, cancel actions."""

    def setUp(self):
        self.client = APIClient()
        self.dev_user, self.org = make_developer()
        self.client.force_authenticate(user=self.dev_user)
        self.campaign = Campaign.objects.create(
            organization=self.org,
            created_by=self.dev_user,
            name='Action Test',
            message_template='Hi there',
        )

    def test_schedule_sets_scheduled_status(self):
        future = (timezone.now() + timezone.timedelta(hours=2)).isoformat()
        r = self.client.post(
            f'/api/v1/campaigns/{self.campaign.id}/schedule/',
            {'scheduled_at': future},
            format='json',
        )
        self.assertEqual(r.status_code, 200)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.SCHEDULED)

    def test_schedule_rejects_past_datetime(self):
        past = (timezone.now() - timezone.timedelta(hours=1)).isoformat()
        r = self.client.post(
            f'/api/v1/campaigns/{self.campaign.id}/schedule/',
            {'scheduled_at': past},
            format='json',
        )
        self.assertEqual(r.status_code, 400)

    def test_cancel_scheduled_campaign(self):
        self.campaign.status = Campaign.Status.SCHEDULED
        self.campaign.save()
        r = self.client.post(f'/api/v1/campaigns/{self.campaign.id}/cancel/')
        self.assertEqual(r.status_code, 200)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.CANCELLED)

    def test_cancel_draft_fails(self):
        r = self.client.post(f'/api/v1/campaigns/{self.campaign.id}/cancel/')
        self.assertEqual(r.status_code, 400)

    @patch('apps.campaigns.tasks.dispatch_campaign_to_recipients')
    def test_send_now_queues_task(self, mock_task):
        mock_task.delay = MagicMock()
        r = self.client.post(f'/api/v1/campaigns/{self.campaign.id}/send/')
        self.assertEqual(r.status_code, 200)
        mock_task.delay.assert_called_once_with(str(self.campaign.id))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.SENDING)

    def test_send_sent_campaign_fails(self):
        self.campaign.status = Campaign.Status.SENT
        self.campaign.save()
        r = self.client.post(f'/api/v1/campaigns/{self.campaign.id}/send/')
        self.assertEqual(r.status_code, 400)

    @patch('apps.campaigns.tasks.dispatch_campaign_to_recipients')
    def test_send_already_sending_campaign_fails(self, mock_task):
        """Second send while campaign is SENDING must return 400 (not double-dispatch)."""
        mock_task.delay = MagicMock()
        self.campaign.status = Campaign.Status.SENDING
        self.campaign.save()
        r = self.client.post(f'/api/v1/campaigns/{self.campaign.id}/send/')
        self.assertEqual(r.status_code, 400)
        mock_task.delay.assert_not_called()


@override_settings(CACHES=_LOCMEM)
class CampaignSendRaceTest(TransactionTestCase):
    """select_for_update() prevents concurrent send from double-dispatching the task."""

    def setUp(self):
        self.dev_user, self.org = make_developer()
        self.campaign = Campaign.objects.create(
            organization=self.org,
            created_by=self.dev_user,
            name='Race Test',
            message_template='Hello',
        )

    @patch('apps.campaigns.tasks.dispatch_campaign_to_recipients')
    def test_concurrent_send_dispatches_task_exactly_once(self, mock_task):
        mock_task.delay = MagicMock()
        results = []

        def do_send():
            c = APIClient()
            c.force_authenticate(user=self.dev_user)
            r = c.post(f'/api/v1/campaigns/{self.campaign.id}/send/')
            results.append(r.status_code)

        t1 = threading.Thread(target=do_send)
        t2 = threading.Thread(target=do_send)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one request must succeed and one must be rejected
        self.assertIn(200, results)
        self.assertIn(400, results)
        # Task dispatched exactly once
        self.assertEqual(mock_task.delay.call_count, 1)


@override_settings(CACHES=_LOCMEM, CELERY_TASK_ALWAYS_EAGER=True)
class CampaignDispatchTaskTests(TestCase):
    """dispatch_campaign_to_recipients task fans out messages via CampaignOrchestrator."""

    def setUp(self):
        self.dev_user, self.org = make_developer()
        self.campaign = Campaign.objects.create(
            organization=self.org,
            created_by=self.dev_user,
            name='Dispatch Test',
            message_template='Test broadcast',
            status=Campaign.Status.SENDING,
        )
        self.lead_user = make_user(phone='+923001111111', role='client')
        from apps.leads.models import Lead
        Lead.objects.create(
            user=self.lead_user,
            organization=self.org,
            status='warm',
        )

    def _mock_dispatch_one_sent(self, recipient, wa_client, limiter):
        from apps.campaigns.models import CampaignRecipient
        from django.utils import timezone as tz
        recipient.delivery_status = CampaignRecipient.DeliveryStatus.SENT
        recipient.sent_at = tz.now()
        recipient.save(update_fields=['delivery_status', 'sent_at'])

    def _mock_dispatch_one_failed(self, recipient, wa_client, limiter):
        from apps.campaigns.models import CampaignRecipient
        recipient.delivery_status = CampaignRecipient.DeliveryStatus.FAILED
        recipient.error_message = 'WA error'
        recipient.save(update_fields=['delivery_status', 'error_message'])

    @patch('apps.campaigns.campaign_manager.CampaignOrchestrator.dispatch_one')
    @patch('apps.whatsapp.client.get_wa_client')
    def test_sends_to_all_leads(self, mock_factory, mock_dispatch):
        mock_factory.return_value = MagicMock()
        mock_dispatch.side_effect = self._mock_dispatch_one_sent

        from apps.campaigns.tasks import dispatch_campaign_to_recipients
        dispatch_campaign_to_recipients(str(self.campaign.id))

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.SENT)
        self.assertEqual(self.campaign.sent_count, 1)
        self.assertEqual(self.campaign.failed_count, 0)

    @patch('apps.campaigns.campaign_manager.CampaignOrchestrator.dispatch_one')
    @patch('apps.whatsapp.client.get_wa_client')
    def test_failed_sends_tracked(self, mock_factory, mock_dispatch):
        mock_factory.return_value = MagicMock()
        mock_dispatch.side_effect = self._mock_dispatch_one_failed

        from apps.campaigns.tasks import dispatch_campaign_to_recipients
        dispatch_campaign_to_recipients(str(self.campaign.id))

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.FAILED)
        self.assertEqual(self.campaign.failed_count, 1)

    @patch('apps.whatsapp.client.get_wa_client')
    def test_skips_non_sending_campaign(self, mock_factory):
        self.campaign.status = Campaign.Status.DRAFT
        self.campaign.save()

        from apps.campaigns.tasks import dispatch_campaign_to_recipients
        result = dispatch_campaign_to_recipients(str(self.campaign.id))

        mock_factory.assert_not_called()
        self.assertIsNone(result)

    @patch('apps.campaigns.campaign_manager.CampaignOrchestrator.dispatch_one')
    @patch('apps.whatsapp.client.get_wa_client')
    def test_zero_recipients_marked_failed(self, mock_factory, mock_dispatch):
        """Campaign with no matching leads must be marked FAILED (A9-LOGIC-1)."""
        from apps.leads.models import Lead
        Lead.objects.filter(organization=self.org).delete()

        from apps.campaigns.tasks import dispatch_campaign_to_recipients
        dispatch_campaign_to_recipients(str(self.campaign.id))

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.sent_count, 0)
        self.assertEqual(self.campaign.status, Campaign.Status.FAILED)
        mock_dispatch.assert_not_called()

    @patch('apps.campaigns.tasks.dispatch_campaign_to_recipients')
    def test_dispatch_scheduled_fires_due_campaigns(self, mock_send):
        mock_send.delay = MagicMock()
        self.campaign.status = Campaign.Status.SCHEDULED
        self.campaign.scheduled_at = timezone.now() - timezone.timedelta(minutes=1)
        self.campaign.save()

        from apps.campaigns.tasks import dispatch_scheduled_campaigns
        count = dispatch_scheduled_campaigns()

        self.assertEqual(count, 1)
        mock_send.delay.assert_called_once_with(str(self.campaign.id))
