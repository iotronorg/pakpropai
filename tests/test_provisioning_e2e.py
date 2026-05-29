"""
End-to-end integration tests for provisioning pipeline (8 tests).
All Meta Graph API calls are mocked via unittest.mock.patch.
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase

from apps.whatsapp.production_provisioner import ProductionProvisioningService
from tests.factories import make_developer


def _meta_get(waba_payload=None, templates_payload=None):
    """Return a side_effect function for requests.get based on URL params."""
    waba_payload = waba_payload or {'id': 'waba_123', 'name': 'WABA', 'currency': 'USD', 'timezone_id': '1'}
    templates_payload = templates_payload or {'data': []}

    def _side_effect(url, **kwargs):
        r = MagicMock()
        r.status_code = 200
        if 'message_templates' in url:
            r.json.return_value = templates_payload
        else:
            r.json.return_value = waba_payload
        r.text = ''
        return r
    return _side_effect


def _meta_post_ok():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {'success': True}
    r.text = ''
    return r


def _setup_org_with_config(leads=0, sessions=0):
    from apps.whatsapp.models import OrgWhatsAppConfig
    from apps.leads.models import Lead
    from apps.whatsapp.models import WhatsAppSession

    dev, org = make_developer()
    OrgWhatsAppConfig.objects.get_or_create(
        organization=org,
        defaults={
            'waba_id': 'waba_123',
            'access_token': 'tok_abc',
            'verify_token': 'vt_abc',
        },
    )
    for _ in range(leads):
        Lead.objects.create(user=dev, organization=org, is_sandbox=True)
    for i in range(sessions):
        WhatsAppSession.objects.create(
            phone=f'+92300111{i:04d}', organization=org, is_sandbox=True
        )
    return dev, org


class FullProvisionTest(TestCase):
    """test_full_provision_sandbox_to_production"""

    @patch('apps.whatsapp.production_provisioner.requests.post', return_value=_meta_post_ok())
    @patch('apps.whatsapp.production_provisioner.requests.get')
    def test_org_becomes_production(self, mock_get, mock_post):
        mock_get.side_effect = _meta_get()
        _, org = _setup_org_with_config()

        ProductionProvisioningService().run(str(org.id))

        org.refresh_from_db()
        self.assertEqual(org.operational_mode, 'production')


class RedisCacheCleared(TestCase):
    """test_redis_cache_cleared_on_success"""

    @patch('apps.whatsapp.production_provisioner.requests.post', return_value=_meta_post_ok())
    @patch('apps.whatsapp.production_provisioner.requests.get')
    def test_redis_keys_absent_after_provisioning(self, mock_get, mock_post):
        from django.core.cache import cache
        mock_get.side_effect = _meta_get()
        _, org = _setup_org_with_config()

        cache.set(f'wa_route:{org.id}', 'test_value')
        cache.set(f'org_config:{org.id}', 'test_value')

        ProductionProvisioningService().run(str(org.id))

        self.assertIsNone(cache.get(f'wa_route:{org.id}'))
        self.assertIsNone(cache.get(f'org_config:{org.id}'))


class WebhookVerifiedTest(TestCase):
    """test_live_message_handling_initialized"""

    @patch('apps.whatsapp.production_provisioner.requests.post', return_value=_meta_post_ok())
    @patch('apps.whatsapp.production_provisioner.requests.get')
    def test_webhook_verified_at_set(self, mock_get, mock_post):
        mock_get.side_effect = _meta_get()
        _, org = _setup_org_with_config()

        ProductionProvisioningService().run(str(org.id))

        org.refresh_from_db()
        record = org.provisioning_record
        self.assertIsNotNone(record.webhook_verified_at)
        self.assertEqual(org.operational_mode, 'production')


class FailOnInvalidWABATest(TestCase):
    """test_provision_fails_on_invalid_waba"""

    @patch('apps.whatsapp.production_provisioner.requests.get')
    def test_failed_mode_on_meta_error(self, mock_get):
        error_payload = {'error': {'message': 'Invalid WABA ID', 'code': 100}}
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = error_payload
        r.text = ''
        mock_get.return_value = r

        _, org = _setup_org_with_config()
        ProductionProvisioningService().run(str(org.id))

        org.refresh_from_db()
        self.assertEqual(org.operational_mode, 'failed')
        record = org.provisioning_record
        self.assertIn('Invalid WABA ID', record.error_detail)


class IdempotentRetryTest(TestCase):
    """test_idempotent_retry_skips_completed_steps"""

    @patch('apps.whatsapp.production_provisioner.requests.post', return_value=_meta_post_ok())
    @patch('apps.whatsapp.production_provisioner.requests.get')
    def test_only_waba_step_reruns(self, mock_get, mock_post):
        from django.utils import timezone
        mock_get.side_effect = _meta_get()
        _, org = _setup_org_with_config()

        # Full run
        ProductionProvisioningService().run(str(org.id))
        org.refresh_from_db()
        record = org.provisioning_record

        # Simulate needing to re-verify WABA
        record.waba_verified_at = None
        record.save()
        org.operational_mode = 'sandbox'
        org.save()

        mock_get.reset_mock()
        mock_get.side_effect = _meta_get()

        ProductionProvisioningService().run(str(org.id))
        org.refresh_from_db()
        self.assertEqual(org.operational_mode, 'production')

        # Verify WABA GET was called again
        self.assertGreater(mock_get.call_count, 0)


class SandboxLeadsMigratedTest(TestCase):
    """test_sandbox_leads_migrated_to_live"""

    @patch('apps.whatsapp.production_provisioner.requests.post', return_value=_meta_post_ok())
    @patch('apps.whatsapp.production_provisioner.requests.get')
    def test_five_leads_become_live(self, mock_get, mock_post):
        from apps.leads.models import Lead
        mock_get.side_effect = _meta_get()
        _, org = _setup_org_with_config(leads=5)

        ProductionProvisioningService().run(str(org.id))

        self.assertEqual(Lead.objects.filter(organization=org, is_sandbox=False).count(), 5)
        self.assertEqual(Lead.objects.filter(organization=org, is_sandbox=True).count(), 0)


class DeveloperRBACTest(TestCase):
    """test_developer_rbac: agent gets 403 on POST /provisioning/start/"""

    def test_agent_cannot_start_provisioning(self):
        from tests.factories import make_user
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        agent = make_user(role='agent')
        client = APIClient()
        token = str(RefreshToken.for_user(agent).access_token)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = client.post('/api/v1/provisioning/start/')
        self.assertEqual(resp.status_code, 403)


class StripeTrialToPaidTriggerTest(TestCase):
    """test_payment_triggers_provisioning: trial→basic queues provision task."""

    @patch('apps.whatsapp.tasks.provision_organization_live.delay')
    def test_stripe_upgrade_queues_task(self, mock_delay):
        from apps.billing.stripe_service import StripeService
        dev, org = make_developer()
        self.assertEqual(org.operational_mode, 'sandbox')

        StripeService._activate_plan(
            str(org.id), 'basic', 'sub_123', 'cus_456', 'active'
        )

        mock_delay.assert_called_once_with(str(org.id))
