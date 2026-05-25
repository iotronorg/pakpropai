"""
Tests for the notifications feature.
Covers: list/filter, mark-read, user isolation, notification preferences.
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.notifications.models import Notification, UserNotificationPreference
from tests.factories import make_user, make_developer

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _make_notification(user, title='Test', message='msg', is_read=False, status='sent'):
    return Notification.objects.create(
        user=user, title=title, message=message,
        is_read=is_read, status=status, channel='whatsapp',
    )


@override_settings(CACHES=_LOCMEM)
class NotificationListTests(TestCase):
    """GET /notifications/ returns only the authenticated user's notifications."""

    def setUp(self):
        self.client = APIClient()
        self.user = make_user(role='agent')
        self.client.force_authenticate(user=self.user)

    def test_returns_own_notifications(self):
        _make_notification(self.user, title='N1')
        _make_notification(self.user, title='N2')
        r = self.client.get('/api/v1/notifications/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 2)

    def test_unread_count_in_response(self):
        _make_notification(self.user, is_read=False)
        _make_notification(self.user, is_read=True)
        r = self.client.get('/api/v1/notifications/')
        self.assertEqual(r.data['unread_count'], 1)

    def test_unread_filter(self):
        _make_notification(self.user, title='Unread', is_read=False)
        _make_notification(self.user, title='Read',   is_read=True)
        r = self.client.get('/api/v1/notifications/', {'unread': 'true'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['title'], 'Unread')

    def test_pagination_limit_offset(self):
        for i in range(5):
            _make_notification(self.user, title=f'N{i}')
        r = self.client.get('/api/v1/notifications/', {'limit': 2, 'offset': 0})
        self.assertEqual(r.data['count'], 5)
        self.assertEqual(len(r.data['results']), 2)

    def test_bad_limit_returns_400(self):
        r = self.client.get('/api/v1/notifications/', {'limit': 'abc'})
        self.assertEqual(r.status_code, 400)

    def test_bad_offset_returns_400(self):
        r = self.client.get('/api/v1/notifications/', {'offset': 'xyz'})
        self.assertEqual(r.status_code, 400)

    def test_unauthenticated_returns_401(self):
        r = APIClient().get('/api/v1/notifications/')
        self.assertEqual(r.status_code, 401)

    def test_does_not_return_other_users_notifications(self):
        other = make_user(phone='+923009000001', role='agent')
        _make_notification(other, title='Other')
        r = self.client.get('/api/v1/notifications/')
        self.assertEqual(r.data['count'], 0)


@override_settings(CACHES=_LOCMEM)
class MarkReadTests(TestCase):
    """POST /notifications/mark-read/ marks specific or all notifications as read."""

    def setUp(self):
        self.client = APIClient()
        self.user = make_user(role='agent')
        self.client.force_authenticate(user=self.user)
        self.n1 = _make_notification(self.user, title='N1', is_read=False)
        self.n2 = _make_notification(self.user, title='N2', is_read=False)

    def test_mark_specific_ids(self):
        r = self.client.post(
            '/api/v1/notifications/mark-read/',
            {'ids': [str(self.n1.id)]},
            format='json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['marked_read'], 1)
        self.n1.refresh_from_db()
        self.assertTrue(self.n1.is_read)
        self.n2.refresh_from_db()
        self.assertFalse(self.n2.is_read)

    def test_mark_all_read(self):
        r = self.client.post('/api/v1/notifications/mark-read/', {}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['marked_read'], 2)
        self.assertTrue(Notification.objects.filter(user=self.user, is_read=False).count() == 0)

    def test_cannot_mark_another_users_notifications(self):
        other = make_user(phone='+923009000002', role='agent')
        other_notif = _make_notification(other, is_read=False)
        r = self.client.post(
            '/api/v1/notifications/mark-read/',
            {'ids': [str(other_notif.id)]},
            format='json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['marked_read'], 0)
        other_notif.refresh_from_db()
        self.assertFalse(other_notif.is_read)

    def test_already_read_not_double_counted(self):
        self.n1.is_read = True
        self.n1.save()
        r = self.client.post('/api/v1/notifications/mark-read/', {}, format='json')
        self.assertEqual(r.data['marked_read'], 1)


@override_settings(CACHES=_LOCMEM)
class NotificationPreferencesTests(TestCase):
    """GET/PATCH /auth/me/notification-preferences/"""

    def setUp(self):
        self.client = APIClient()
        self.user = make_user(role='developer')
        self.client.force_authenticate(user=self.user)

    def test_get_creates_defaults_on_first_fetch(self):
        r = self.client.get('/api/v1/auth/me/notification-preferences/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['whatsapp_enabled'])
        self.assertTrue(r.data['lead_updates'])
        self.assertFalse(r.data['marketing'])

    def test_patch_toggles_preference(self):
        r = self.client.patch(
            '/api/v1/auth/me/notification-preferences/',
            {'marketing': True, 'lead_updates': False},
            format='json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['marketing'])
        self.assertFalse(r.data['lead_updates'])

    def test_patch_persists(self):
        self.client.patch(
            '/api/v1/auth/me/notification-preferences/',
            {'marketing': True},
            format='json',
        )
        r = self.client.get('/api/v1/auth/me/notification-preferences/')
        self.assertTrue(r.data['marketing'])

    def test_unauthenticated_returns_401(self):
        r = APIClient().get('/api/v1/auth/me/notification-preferences/')
        self.assertEqual(r.status_code, 401)
