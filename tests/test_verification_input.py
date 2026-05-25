"""
Input-validation tests for verification views (A9-SEC-2).
Covers: unguarded int() casts that previously raised 500 on bad input.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from tests.factories import make_user


class FraudAlertsInputTest(TestCase):
    """GET /verification/fraud/alerts/ — bad `limit` must return 400, not 500."""

    def setUp(self):
        self.client = APIClient()
        admin = make_user(role='admin')
        self.client.force_authenticate(user=admin)

    def test_bad_limit_returns_400(self):
        r = self.client.get('/api/v1/verification/fraud/alerts/', {'limit': 'abc'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('limit', r.data.get('detail', ''))

    def test_valid_limit_returns_200(self):
        r = self.client.get('/api/v1/verification/fraud/alerts/', {'limit': '10'})
        self.assertEqual(r.status_code, 200)

    def test_non_admin_returns_403(self):
        agent = make_user(role='agent')
        c = APIClient()
        c.force_authenticate(user=agent)
        r = c.get('/api/v1/verification/fraud/alerts/')
        self.assertEqual(r.status_code, 403)


class FraudBlacklistInputTest(TestCase):
    """POST /verification/fraud/blacklist/ — bad `ttl_days` must return 400, not 500."""

    def setUp(self):
        self.client = APIClient()
        admin = make_user(role='admin')
        self.client.force_authenticate(user=admin)

    def test_bad_ttl_days_returns_400(self):
        r = self.client.post(
            '/api/v1/verification/fraud/blacklist/',
            {'token': '+15550001234', 'ttl_days': 'notanumber'},
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn('ttl_days', r.data.get('detail', ''))

    def test_valid_ttl_days_accepted(self):
        r = self.client.post(
            '/api/v1/verification/fraud/blacklist/',
            {'token': '+15550009999', 'ttl_days': 30},
            format='json',
        )
        self.assertIn(r.status_code, (200, 201))

    def test_missing_token_returns_400(self):
        r = self.client.post(
            '/api/v1/verification/fraud/blacklist/',
            {'ttl_days': 7},
            format='json',
        )
        self.assertEqual(r.status_code, 400)

    def test_ttl_days_over_limit_returns_400(self):
        """ttl_days > 3650 must return 400, not OverflowError (A9-LOGIC-3)."""
        r = self.client.post(
            '/api/v1/verification/fraud/blacklist/',
            {'token': '+15550001111', 'ttl_days': 99999999},
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn('ttl_days', r.data.get('detail', ''))

    def test_ttl_days_at_limit_accepted(self):
        r = self.client.post(
            '/api/v1/verification/fraud/blacklist/',
            {'token': '+15550002222', 'ttl_days': 3650},
            format='json',
        )
        self.assertIn(r.status_code, (200, 201))
