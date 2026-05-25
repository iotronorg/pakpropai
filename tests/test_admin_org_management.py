"""
Admin org management tests — FEATURE-ADMIN-ORG-MANAGEMENT.

Coverage:
  - List: admin sees all, developer sees own, agent gets 403
  - Search + is_active filter
  - Usage counts included in list response
  - Create: admin ok, developer 403
  - Suspend: admin ok, developer 403, already-suspended 400
  - Activate: admin ok, developer 403, already-active 400
  - Update (PATCH): admin can update fields
"""
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from tests.factories import make_agent, make_client, make_developer, make_user

LIST_URL     = "/api/v1/organizations/"
DETAIL_URL   = lambda pk: f"/api/v1/organizations/{pk}/"
SUSPEND_URL  = lambda pk: f"/api/v1/organizations/{pk}/suspend/"
ACTIVATE_URL = lambda pk: f"/api/v1/organizations/{pk}/activate/"


class _Base(APITestCase):
    def setUp(self):
        self.admin               = make_user(role='admin')
        self.dev_user,  self.org  = make_developer(org_name='Org Alpha')
        self.dev2_user, self.org2 = make_developer(org_name='Org Beta')
        _, self.agent            = make_agent(org=self.org)

    def _auth(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c


# ── List ───────────────────────────────────────────────────────────────────────

class OrgListTest(_Base):

    def test_admin_sees_all_orgs(self):
        resp = self._auth(self.admin).get(LIST_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(str(self.org.pk), ids)
        self.assertIn(str(self.org2.pk), ids)

    def test_developer_sees_only_own_org(self):
        resp = self._auth(self.dev_user).get(LIST_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(str(self.org.pk), ids)
        self.assertNotIn(str(self.org2.pk), ids)

    def test_agent_cannot_list_orgs(self):
        resp = self._auth(self.agent.user).get(LIST_URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_search_filter(self):
        resp = self._auth(self.admin).get(LIST_URL + '?search=Alpha')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [r['name'] for r in resp.data['results']]
        self.assertIn('Org Alpha', names)
        self.assertNotIn('Org Beta', names)

    def test_is_active_filter_false(self):
        self.org2.is_active = False
        self.org2.save(update_fields=['is_active'])
        resp = self._auth(self.admin).get(LIST_URL + '?is_active=false')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in resp.data['results']]
        self.assertIn(str(self.org2.pk), ids)
        self.assertNotIn(str(self.org.pk), ids)

    def test_list_includes_usage_counts(self):
        resp = self._auth(self.admin).get(LIST_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        row = next(r for r in resp.data['results'] if r['id'] == str(self.org.pk))
        self.assertIn('agent_count', row)
        self.assertIn('lead_count', row)
        self.assertIn('property_count', row)
        self.assertIsInstance(row['agent_count'], int)


# ── Create ─────────────────────────────────────────────────────────────────────

class OrgCreateTest(_Base):

    def test_admin_can_create_org(self):
        new_dev = make_user(role='developer')
        payload = {
            'name':       'New Org',
            'org_type':   'agency',
            'plan':       'trial',
            'country':    'PK',
            'admin_user': str(new_dev.pk),
        }
        resp = self._auth(self.admin).post(LIST_URL, payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'New Org')

    def test_developer_cannot_create_org(self):
        payload = {'name': 'Sneaky Org', 'org_type': 'agency', 'plan': 'trial', 'country': 'PK'}
        resp = self._auth(self.dev_user).post(LIST_URL, payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ── Suspend ────────────────────────────────────────────────────────────────────

class OrgSuspendTest(_Base):

    def test_admin_can_suspend_active_org(self):
        resp = self._auth(self.admin).post(SUSPEND_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)

    def test_suspend_already_suspended_returns_400(self):
        self.org.is_active = False
        self.org.save(update_fields=['is_active'])
        resp = self._auth(self.admin).post(SUSPEND_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already suspended', resp.data['detail'])

    def test_developer_cannot_suspend_org(self):
        resp = self._auth(self.dev_user).post(SUSPEND_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_suspend_nonexistent_org_returns_404(self):
        resp = self._auth(self.admin).post(SUSPEND_URL('00000000-0000-0000-0000-000000000000'))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ── Activate ───────────────────────────────────────────────────────────────────

class OrgActivateTest(_Base):

    def test_admin_can_activate_suspended_org(self):
        self.org.is_active = False
        self.org.save(update_fields=['is_active'])
        resp = self._auth(self.admin).post(ACTIVATE_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_active)

    def test_activate_already_active_returns_400(self):
        resp = self._auth(self.admin).post(ACTIVATE_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already active', resp.data['detail'])

    def test_developer_cannot_activate_org(self):
        self.org.is_active = False
        self.org.save(update_fields=['is_active'])
        resp = self._auth(self.dev_user).post(ACTIVATE_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ── Update ─────────────────────────────────────────────────────────────────────

class OrgUpdateTest(_Base):

    def test_admin_can_update_org(self):
        resp = self._auth(self.admin).patch(
            DETAIL_URL(self.org.pk),
            {'name': 'Updated Alpha'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Updated Alpha')


# ── Admin org config ────────────────────────────────────────────────────────────

CONFIG_URL      = lambda pk: f"/api/v1/organizations/{pk}/config/"
CONFIG_KEY_URL  = lambda pk, key: f"/api/v1/organizations/{pk}/config/{key}/"


class AdminOrgConfigTest(_Base):

    def test_admin_can_get_org_config(self):
        resp = self._auth(self.admin).get(CONFIG_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('features', resp.data)
        self.assertIn('overrides', resp.data)
        self.assertIn('allowed_keys', resp.data)

    def test_admin_can_patch_org_config(self):
        resp = self._auth(self.admin).patch(
            CONFIG_URL(self.org.pk),
            {'feature_deal_lock': False},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['features']['feature_deal_lock'])

    def test_admin_can_reset_org_config_key(self):
        self._auth(self.admin).patch(CONFIG_URL(self.org.pk), {'feature_deal_lock': False}, format='json')
        resp = self._auth(self.admin).delete(CONFIG_KEY_URL(self.org.pk, 'feature_deal_lock'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_developer_cannot_access_admin_org_config(self):
        resp = self._auth(self.dev_user).get(CONFIG_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_forbidden(self):
        resp = APIClient().get(CONFIG_URL(self.org.pk))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_patch_invalid_key_returns_400(self):
        resp = self._auth(self.admin).patch(
            CONFIG_URL(self.org.pk),
            {'invalid_key': True},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
