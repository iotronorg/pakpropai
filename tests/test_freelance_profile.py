"""
Tests for POST /api/v1/agents/freelance-profile/
"""
from django.test import TestCase
from rest_framework.test import APIClient
from apps.agents.models import FreelanceAgentProfile
from apps.users.models import User


def _user(phone, role='agent', name='Test Agent'):
    u = User.objects.create_user(phone=phone, name=name, role=role)
    u.is_phone_verified = True
    u.save(update_fields=['is_phone_verified'])
    return u


class FreelanceProfileCreateTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.agent = _user('+923010000001')
        self.client.force_authenticate(user=self.agent)

    def test_create_profile_without_license(self):
        r = self.client.post('/api/v1/agents/freelance-profile/', {})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['verification_status'], 'unverified')
        self.assertTrue(FreelanceAgentProfile.objects.filter(user=self.agent).exists())

    def test_create_profile_with_license(self):
        r = self.client.post('/api/v1/agents/freelance-profile/', {'license_number': 'RERA-12345'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['verification_status'], 'pending')
        self.assertEqual(r.data['license_number'], 'RERA-12345')

    def test_idempotent_update_existing(self):
        self.client.post('/api/v1/agents/freelance-profile/', {})
        r = self.client.post('/api/v1/agents/freelance-profile/', {'license_number': 'RERA-99999'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['license_number'], 'RERA-99999')
        self.assertEqual(FreelanceAgentProfile.objects.filter(user=self.agent).count(), 1)

    def test_non_agent_role_returns_403(self):
        dev = _user('+923010000002', role='developer', name='Dev')
        self.client.force_authenticate(user=dev)
        r = self.client.post('/api/v1/agents/freelance-profile/', {})
        self.assertEqual(r.status_code, 403)

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        r = self.client.post('/api/v1/agents/freelance-profile/', {})
        self.assertEqual(r.status_code, 401)
