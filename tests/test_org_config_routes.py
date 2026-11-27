"""T-1091 — organization feature-flag routes split: one view used to serve both the
collection and the ``<key>/`` route, so DELETE without a key (and GET/PATCH with one) were 500s."""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.organizations.models import OrganizationConfig
from tests.factories import make_developer, make_user

KEY = 'feature_tax_advice'


class OrgConfigRoutesTest(TestCase):
    def setUp(self):
        self.owner, self.org = make_developer()
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def test_own_config_collection_and_key_routes(self):
        url = '/api/v1/organizations/me/config/'
        r = self.api.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn(KEY, r.data['allowed_keys'])
        r = self.api.patch(url, {KEY: False}, format='json')
        self.assertEqual((r.status_code, r.data['features'][KEY]), (200, False))
        self.assertEqual(self.api.delete(url).status_code, 405)

        item = f'{url}{KEY}/'
        self.assertEqual(self.api.get(item).status_code, 405)
        self.assertEqual(self.api.patch(item, {}, format='json').status_code, 405)
        self.assertEqual(self.api.delete(item).status_code, 200)
        self.assertFalse(OrganizationConfig.objects.filter(organization=self.org, key=KEY).exists())
        self.assertEqual(self.api.delete(f'{url}not_a_key/').status_code, 400)

    def test_admin_config_collection_and_key_routes(self):
        api = APIClient()
        api.force_authenticate(make_user(role='admin', is_staff=True))
        url = f'/api/v1/organizations/{self.org.pk}/config/'
        self.assertEqual(api.patch(url, {KEY: True}, format='json').status_code, 200)
        self.assertEqual(api.delete(url).status_code, 405)
        self.assertEqual(api.get(f'{url}{KEY}/').status_code, 405)
        self.assertEqual(api.delete(f'{url}{KEY}/').status_code, 200)
        self.assertFalse(OrganizationConfig.objects.filter(organization=self.org, key=KEY).exists())
