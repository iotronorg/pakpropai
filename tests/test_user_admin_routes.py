"""T-1091 — `/auth/users/` split into list and detail views (a missing `pk` used to be a 500)."""
from django.test import TestCase
from rest_framework.test import APIClient

from tests.factories import make_client, make_user

URL = '/api/v1/auth/users/'


class UserAdminRoutesTest(TestCase):
    def setUp(self):
        self.admin = make_user(role='admin', is_staff=True)
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.other = make_client()

    def test_collection_lists_and_refuses_item_methods(self):
        r = self.api.get(URL)
        self.assertEqual((r.status_code, r.data['count'] >= 2), (200, True))
        for method in ('patch', 'delete'):
            self.assertEqual(getattr(self.api, method)(URL, {}, format='json').status_code, 405, method)

    def test_detail_updates_deletes_and_refuses_collection_methods(self):
        item = f'{URL}{self.other.pk}/'
        self.assertEqual(self.api.get(item).status_code, 405)
        self.assertEqual(self.api.post(item, {}, format='json').status_code, 405)
        r = self.api.patch(item, {'name': 'Renamed'}, format='json')
        self.assertEqual((r.status_code, r.data['name']), (200, 'Renamed'))
        r = self.api.delete(f'{URL}{self.admin.pk}/')
        self.assertEqual((r.status_code, r.data['error_info']['code']), (400, 'users.cannot_delete_self'))
        self.assertEqual(self.api.delete(item).status_code, 204)

    def test_non_admin_is_refused_by_the_permission(self):
        # ``IsAdminUser`` (apps.core.permissions) already requires role admin, so the view's own
        # ``users.admin_required`` check is only a second line of defence.
        api = APIClient()
        api.force_authenticate(make_user(role='agent', is_staff=True))
        r = api.get(URL)
        self.assertEqual((r.status_code, r.data['error_info']['code']), (403, 'permission_denied'))
