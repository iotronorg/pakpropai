"""
Unit tests for MetaHandshakeClient, SandboxDataMigrator,
and ProductionProvisioningService (12 tests).
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase

from apps.whatsapp.production_provisioner import (
    MetaHandshakeClient,
    SandboxDataMigrator,
    ProductionProvisioningService,
    ProvisioningError,
)
from tests.factories import make_developer


def _mock_response(status_code=200, json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.text = str(json_data)
    r.json.return_value = json_data or {}
    return r


class MetaVerifyWABATest(TestCase):
    """verify_waba: success, 400, invalid token."""

    def test_success_returns_dict(self):
        client = MetaHandshakeClient()
        payload = {'id': '123', 'name': 'Test WABA', 'currency': 'USD', 'timezone_id': '1'}
        with patch('apps.whatsapp.production_provisioner.requests.get',
                   return_value=_mock_response(200, payload)):
            result = client.verify_waba('123', 'token')
        self.assertEqual(result['id'], '123')

    def test_400_raises_provisioning_error(self):
        client = MetaHandshakeClient()
        with patch('apps.whatsapp.production_provisioner.requests.get',
                   return_value=_mock_response(400, {'error': {'message': 'bad request'}})):
            with self.assertRaises(ProvisioningError) as ctx:
                client.verify_waba('bad_id', 'token')
        self.assertEqual(ctx.exception.step, 'waba')

    def test_invalid_token_raises_provisioning_error(self):
        client = MetaHandshakeClient()
        error_payload = {'error': {'message': 'Invalid OAuth access token', 'code': 190}}
        with patch('apps.whatsapp.production_provisioner.requests.get',
                   return_value=_mock_response(200, error_payload)):
            with self.assertRaises(ProvisioningError) as ctx:
                client.verify_waba('123', 'bad_token')
        self.assertIn('Invalid OAuth', ctx.exception.detail)


class MetaRegisterWebhookTest(TestCase):
    """register_webhook: success, already subscribed (idempotent)."""

    def test_success_returns_true(self):
        client = MetaHandshakeClient()
        with patch('apps.whatsapp.production_provisioner.requests.post',
                   return_value=_mock_response(200, {'success': True})):
            result = client.register_webhook('123', 'token', 'https://example.com/wh', 'verify')
        self.assertTrue(result)

    def test_already_subscribed_is_idempotent(self):
        client = MetaHandshakeClient()
        already = {'error': {'code': 100, 'message': 'already subscribed'}}
        with patch('apps.whatsapp.production_provisioner.requests.post',
                   return_value=_mock_response(400, already)):
            result = client.register_webhook('123', 'token', 'https://example.com/wh', 'verify')
        self.assertTrue(result)


class MetaGetTemplatesTest(TestCase):
    """get_approved_templates: success, empty list."""

    def test_returns_template_list(self):
        client = MetaHandshakeClient()
        templates = [{'name': 'hello_world', 'status': 'APPROVED'}]
        with patch('apps.whatsapp.production_provisioner.requests.get',
                   return_value=_mock_response(200, {'data': templates})):
            result = client.get_approved_templates('123', 'token')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'hello_world')

    def test_empty_list_returns_empty(self):
        client = MetaHandshakeClient()
        with patch('apps.whatsapp.production_provisioner.requests.get',
                   return_value=_mock_response(200, {'data': []})):
            result = client.get_approved_templates('123', 'token')
        self.assertEqual(result, [])


class SandboxDataMigratorTest(TestCase):
    """migrate: migrates leads/sessions/properties, returns correct counts."""

    def test_migrates_leads(self):
        from apps.leads.models import Lead
        dev, org = make_developer()
        Lead.objects.create(user=dev, organization=org, is_sandbox=True)
        Lead.objects.create(user=dev, organization=org, is_sandbox=True)

        migrator = SandboxDataMigrator()
        result = migrator.migrate(org)

        self.assertEqual(result.leads_count, 2)
        self.assertEqual(Lead.objects.filter(organization=org, is_sandbox=False).count(), 2)

    def test_migrates_sessions(self):
        from apps.whatsapp.models import WhatsAppSession
        dev, org = make_developer()
        WhatsAppSession.objects.create(phone='+923001111111', organization=org, is_sandbox=True)
        WhatsAppSession.objects.create(phone='+923001111112', organization=org, is_sandbox=True)
        WhatsAppSession.objects.create(phone='+923001111113', organization=org, is_sandbox=True)

        migrator = SandboxDataMigrator()
        result = migrator.migrate(org)

        self.assertEqual(result.sessions_count, 3)
        self.assertFalse(
            WhatsAppSession.objects.filter(organization=org, is_sandbox=True).exists()
        )

    def test_migrates_properties_returns_correct_counts(self):
        from apps.properties.models import Property
        from tests.factories import make_property
        dev, org = make_developer()
        for i in range(4):
            p = make_property(org=org, title=f'Prop {i}')
            p.is_sandbox = True
            p.save(update_fields=['is_sandbox'])

        migrator = SandboxDataMigrator()
        result = migrator.migrate(org)

        self.assertEqual(result.properties_count, 4)
        self.assertEqual(Property.objects.filter(organization=org, is_sandbox=False).count(), 4)


class ProductionProvisioningServiceIdempotencyTest(TestCase):
    """ProductionProvisioningService.run: skips already-completed steps."""

    def _setup_org_with_config(self):
        from apps.whatsapp.models import OrgWhatsAppConfig
        dev, org = make_developer()
        OrgWhatsAppConfig.objects.get_or_create(
            organization=org,
            defaults={
                'waba_id': 'waba_123',
                'access_token': 'tok_abc',
                'verify_token': 'vt_abc',
            },
        )
        return org

    @patch('apps.whatsapp.production_provisioner.requests.get')
    @patch('apps.whatsapp.production_provisioner.requests.post')
    def test_skips_completed_waba_step(self, mock_post, mock_get):
        from django.utils import timezone
        from apps.organizations.models import OrgProvisioningRecord

        org = self._setup_org_with_config()
        record = OrgProvisioningRecord.get_or_create_for_org(org)
        record.waba_verified_at = timezone.now()
        record.save()

        template_resp = _mock_response(200, {'data': []})
        mock_get.return_value = template_resp
        mock_post.return_value = _mock_response(200, {'success': True})

        service = ProductionProvisioningService()
        service.run(str(org.id))

        # verify_waba GET call should NOT have been made (step already done)
        # Only template GET should have been called
        calls = [str(c) for c in mock_get.call_args_list]
        waba_calls = [c for c in calls if 'fields=id' in c or 'timezone_id' in c]
        self.assertEqual(len(waba_calls), 0)

    @patch('apps.whatsapp.production_provisioner.requests.get')
    @patch('apps.whatsapp.production_provisioner.requests.post')
    def test_completes_all_fresh_steps_to_production(self, mock_post, mock_get):
        org = self._setup_org_with_config()

        waba_payload = {'id': 'waba_123', 'name': 'Test', 'currency': 'USD', 'timezone_id': '1'}
        templates_payload = {'data': []}

        def side_effect_get(url, **kwargs):
            if 'fields=id' in str(kwargs.get('params', {})):
                return _mock_response(200, waba_payload)
            return _mock_response(200, templates_payload)

        mock_get.side_effect = side_effect_get
        mock_post.return_value = _mock_response(200, {'success': True})

        service = ProductionProvisioningService()
        service.run(str(org.id))

        org.refresh_from_db()
        self.assertEqual(org.operational_mode, 'production')
