"""
API tests for provisioning views (8 tests).
"""

import json
from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIClient

from tests.factories import make_developer, make_user


def _auth(user):
    from rest_framework_simplejwt.tokens import RefreshToken
    client = APIClient()
    token = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


def _setup_org_with_waba(plan='sandbox'):
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
    return dev, org


class StatusNotFoundTest(TestCase):
    """Status 404 before provisioning record exists."""

    def test_returns_404_before_record(self):
        dev, org = _setup_org_with_waba()
        client = _auth(dev)
        resp = client.get('/api/v1/provisioning/status/')
        self.assertEqual(resp.status_code, 404)


class StatusShapeTest(TestCase):
    """Status returns correct shape after provisioning starts."""

    def test_returns_correct_shape(self):
        from apps.organizations.models import OrgProvisioningRecord
        dev, org = _setup_org_with_waba()
        OrgProvisioningRecord.get_or_create_for_org(org)

        client = _auth(dev)
        resp = client.get('/api/v1/provisioning/status/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('operational_mode', data)
        self.assertIn('waba_verified_at', data)
        self.assertIn('sandbox_leads_migrated', data)


class StartReturns202Test(TestCase):
    """Start returns 202 + queues task."""

    @patch('apps.whatsapp.tasks.provision_organization_live.delay')
    def test_202_and_task_queued(self, mock_delay):
        dev, org = _setup_org_with_waba()
        client = _auth(dev)
        resp = client.post('/api/v1/provisioning/start/')
        self.assertEqual(resp.status_code, 202)
        mock_delay.assert_called_once_with(str(org.id))


class StartMissingWABATest(TestCase):
    """Start returns 400 if waba_id missing."""

    def test_400_when_waba_id_missing(self):
        from apps.whatsapp.models import OrgWhatsAppConfig
        dev, org = make_developer()
        OrgWhatsAppConfig.objects.get_or_create(
            organization=org,
            defaults={'waba_id': '', 'access_token': 'tok_abc'},
        )
        client = _auth(dev)
        resp = client.post('/api/v1/provisioning/start/')
        self.assertEqual(resp.status_code, 400)


class StartAlreadyProductionTest(TestCase):
    """Start returns 409 if already production."""

    @patch('apps.whatsapp.tasks.provision_organization_live.delay')
    def test_409_when_already_production(self, mock_delay):
        dev, org = _setup_org_with_waba()
        org.operational_mode = 'production'
        org.save()

        client = _auth(dev)
        resp = client.post('/api/v1/provisioning/start/')
        self.assertEqual(resp.status_code, 409)
        mock_delay.assert_not_called()


class RetryNotFailedTest(TestCase):
    """Retry returns 400 if operational_mode != failed."""

    def test_400_when_not_failed(self):
        from apps.organizations.models import OrgProvisioningRecord
        dev, org = _setup_org_with_waba()
        record = OrgProvisioningRecord.get_or_create_for_org(org)
        record.operational_mode = 'sandbox'
        record.save()

        client = _auth(dev)
        resp = client.post('/api/v1/provisioning/retry/')
        self.assertEqual(resp.status_code, 400)


class RetryQueuedTest(TestCase):
    """Retry queues task when in failed state."""

    @patch('apps.whatsapp.tasks.provision_organization_live.delay')
    def test_retry_queues_task(self, mock_delay):
        from apps.organizations.models import OrgProvisioningRecord
        dev, org = _setup_org_with_waba()
        record = OrgProvisioningRecord.get_or_create_for_org(org)
        record.operational_mode = 'failed'
        record.save()

        client = _auth(dev)
        resp = client.post('/api/v1/provisioning/retry/')
        self.assertEqual(resp.status_code, 202)
        mock_delay.assert_called_once_with(str(org.id))


class CrossOrgIsolationTest(TestCase):
    """Cross-org isolation: org B cannot get org A's provisioning status."""

    def test_org_b_cannot_see_org_a_status(self):
        from apps.organizations.models import OrgProvisioningRecord
        dev_a, org_a = _setup_org_with_waba()
        OrgProvisioningRecord.get_or_create_for_org(org_a)

        dev_b, org_b = make_developer(org_name='Org B')
        client = _auth(dev_b)
        resp = client.get('/api/v1/provisioning/status/')
        self.assertEqual(resp.status_code, 404)
