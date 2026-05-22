"""Tests for data_residency_region auto-assignment and GDPR compliance module."""
from django.test import TestCase
from rest_framework.test import APIClient


# ─── data_residency_region ────────────────────────────────────────────────────

class DataResidencyRegionTest(TestCase):

    def _make_org(self, country, slug):
        from apps.organizations.models import Organization
        return Organization.objects.create(name=f'Org {country}', slug=slug, country=country)

    def test_gb_org_gets_uk_region(self):
        org = self._make_org('GB', 'org-gb')
        self.assertEqual(org.data_residency_region, 'uk')

    def test_ae_org_gets_uae_region(self):
        org = self._make_org('AE', 'org-ae')
        self.assertEqual(org.data_residency_region, 'uae')

    def test_pk_org_gets_pk_region(self):
        org = self._make_org('PK', 'org-pk')
        self.assertEqual(org.data_residency_region, 'pk')

    def test_de_org_gets_eu_region(self):
        org = self._make_org('DE', 'org-de')
        self.assertEqual(org.data_residency_region, 'eu')

    def test_us_org_gets_global_region(self):
        org = self._make_org('US', 'org-us')
        self.assertEqual(org.data_residency_region, 'global')

    def test_fr_org_gets_eu_region(self):
        org = self._make_org('FR', 'org-fr')
        self.assertEqual(org.data_residency_region, 'eu')


# ─── GDPR compliance module ───────────────────────────────────────────────────

class ComplianceModuleTest(TestCase):

    def setUp(self):
        from apps.organizations.models import Organization
        from apps.users.models import User
        self.gb_org = Organization.objects.create(
            name='UK Org', slug='uk-org', country='GB',
        )
        self.user = User.objects.create(phone='+441234567800', role='developer')
        self.gb_org.admin_user = self.user
        self.gb_org.save(update_fields=['admin_user'])
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.user)

    def test_consent_endpoint_exists_for_gdpr_org(self):
        response = self.api_client.post('/api/v1/compliance/consent/', {'purpose': 'marketing'})
        self.assertIn(response.status_code, [200, 201])

    def test_export_endpoint_queues_task(self):
        response = self.api_client.get('/api/v1/compliance/export/')
        self.assertIn(response.status_code, [200, 202])

    def test_compliance_endpoints_404_for_non_gdpr_org(self):
        from apps.organizations.models import Organization
        from apps.users.models import User
        pk_org  = Organization.objects.create(name='PK Org', slug='pk-org', country='PK')
        pk_user = User.objects.create(phone='+923001234567', role='developer')
        pk_org.admin_user = pk_user
        pk_org.save(update_fields=['admin_user'])
        pk_client = APIClient()
        pk_client.force_authenticate(user=pk_user)
        response = pk_client.post('/api/v1/compliance/consent/', {'purpose': 'marketing'})
        self.assertEqual(response.status_code, 404)

    def test_invalid_purpose_returns_400(self):
        response = self.api_client.post('/api/v1/compliance/consent/', {'purpose': 'invalid_thing'})
        self.assertEqual(response.status_code, 400)

    def test_revoke_consent(self):
        self.api_client.post('/api/v1/compliance/consent/', {'purpose': 'analytics'})
        response = self.api_client.post('/api/v1/compliance/consent/', {
            'purpose': 'analytics', 'revoke': True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_active'])
