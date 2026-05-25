"""
Tests for AuditLogView — GET /api/v1/audit-log/

Coverage:
  - Admin can list entries
  - Filter by action, target_model, actor phone
  - Pagination (limit/offset)
  - Developer and agent are forbidden (403)
  - Unauthenticated is 401
"""
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.core.models import AuditLog
from tests.factories import make_agent, make_developer, make_user

URL = "/api/v1/audit-log/"


class _Base(APITestCase):
    def setUp(self):
        self.admin = make_user(role='admin', phone='+923001110001')
        self.dev_user, self.org = make_developer(org_name='Audit Test Org')
        self.agent_user, _ = make_agent(org=self.org)

        # Seed a few log entries
        AuditLog.objects.create(
            actor=self.admin,
            action=AuditLog.Action.CREATE,
            target_model='Organization',
            target_id=str(self.org.pk),
            detail='Created org',
        )
        AuditLog.objects.create(
            actor=self.admin,
            action=AuditLog.Action.CONFIG,
            target_model='SystemConfig',
            target_id='feature_deal_lock',
            detail='Toggled feature flag',
        )
        AuditLog.objects.create(
            actor=self.dev_user,
            action=AuditLog.Action.UPDATE,
            target_model='Organization',
            target_id=str(self.org.pk),
            detail='Updated org name',
        )

    def _auth(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c


class AuditLogAccessTest(_Base):

    def test_admin_can_list(self):
        resp = self._auth(self.admin).get(URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('results', resp.data)
        self.assertGreaterEqual(resp.data['count'], 3)

    def test_developer_forbidden(self):
        resp = self._auth(self.dev_user).get(URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_agent_forbidden(self):
        resp = self._auth(self.agent_user).get(URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_returns_401(self):
        resp = APIClient().get(URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class AuditLogFilterTest(_Base):

    def test_filter_by_action(self):
        resp = self._auth(self.admin).get(URL, {'action': 'config'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for entry in resp.data['results']:
            self.assertEqual(entry['action'], 'config')

    def test_filter_by_target_model(self):
        resp = self._auth(self.admin).get(URL, {'target_model': 'SystemConfig'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for entry in resp.data['results']:
            self.assertEqual(entry['target_model'], 'SystemConfig')

    def test_filter_by_actor_phone(self):
        resp = self._auth(self.admin).get(URL, {'actor': self.admin.phone})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # All returned entries should belong to admin
        for entry in resp.data['results']:
            self.assertEqual(entry['actor_phone'], self.admin.phone)

    def test_filter_by_actor_phone_partial(self):
        # icontains — partial match should work
        resp = self._auth(self.admin).get(URL, {'actor': '110001'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreater(resp.data['count'], 0)


class AuditLogPaginationTest(_Base):

    def test_limit_respected(self):
        resp = self._auth(self.admin).get(URL, {'limit': 1})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['results']), 1)

    def test_offset_advances_page(self):
        resp_all  = self._auth(self.admin).get(URL, {'limit': 10, 'offset': 0})
        resp_page2 = self._auth(self.admin).get(URL, {'limit': 1,  'offset': 1})
        self.assertEqual(resp.status_code if (resp := resp_page2) else 200, status.HTTP_200_OK)
        # Entry at offset=1 should be the second entry from the full list
        self.assertEqual(
            resp_page2.data['results'][0]['id'],
            resp_all.data['results'][1]['id'],
        )

    def test_limit_capped_at_200(self):
        resp = self._auth(self.admin).get(URL, {'limit': 9999})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # limit in response should not exceed 200
        self.assertLessEqual(resp.data['limit'], 200)
