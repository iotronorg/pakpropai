from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.leads.models import Lead
from apps.leads.utils import compute_score_factors, upsert_lead
from apps.organizations.models import Organization, OrganizationMembership

User = get_user_model()


class ComputeScoreFactorsTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            phone='+923001111111', password='pw', role='client'
        )

    def _lead(self, **kwargs):
        defaults = {'user': self.user, 'score': 50}
        defaults.update(kwargs)
        return Lead.objects.create(**defaults)

    def test_compute_score_factors_full(self):
        lead = self._lead(
            intent=Lead.Intent.BUY,
            budget_min=5_000_000,
            budget_max=10_000_000,
            city_interest='Karachi',
        )
        factors = compute_score_factors(lead)
        self.assertEqual(factors['intent'],   25)
        self.assertEqual(factors['budget'],   20)
        self.assertEqual(factors['location'], 15)
        self.assertLessEqual(factors['total'], 100)
        self.assertEqual(
            factors['total'],
            factors['intent'] + factors['budget'] +
            factors['location'] + factors['engagement'] + factors['recency'],
        )

    def test_compute_score_factors_partial(self):
        lead = self._lead(
            intent=Lead.Intent.BUY,
            budget_min=None,
            budget_max=None,
            city_interest='Lahore',
            last_contacted_at=None,
        )
        factors = compute_score_factors(lead)
        self.assertEqual(factors['budget'],     0)
        self.assertEqual(factors['engagement'], 0)
        self.assertEqual(factors['recency'],    0)


class UpsertLeadWritesFactorsTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            phone='+923002222222', password='pw', role='client'
        )

    def test_upsert_lead_writes_factors(self):
        upsert_lead(self.user, intent='buy', city_interest='Karachi')
        lead = Lead.objects.get(user=self.user)
        self.assertIsInstance(lead.score_factors, dict)
        self.assertIn('total', lead.score_factors)
        self.assertGreater(len(lead.score_factors), 0)


class LeadSerializerTest(TestCase):

    def setUp(self):
        self.client_user = User.objects.create_user(
            phone='+923003333333', password='pw', role='client'
        )
        self.dev = User.objects.create_user(
            phone='+923004444444', password='pw', role='developer'
        )
        self.org = Organization.objects.create(name='Test Org', admin_user=self.dev)
        OrganizationMembership.objects.create(
            user=self.dev,
            organization=self.org,
            role=OrganizationMembership.Role.OWNER,
            is_active=True,
        )
        self.lead = Lead.objects.create(
            user=self.client_user,
            organization=self.org,
            intent=Lead.Intent.BUY,
            score_factors={
                'intent': 25, 'budget': 0, 'location': 15,
                'engagement': 0, 'recency': 0, 'total': 40,
            },
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.dev)

    def test_score_factors_in_serializer(self):
        resp = self.api.get(f'/api/v1/leads/{self.lead.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('score_factors', resp.data)
        self.assertEqual(resp.data['score_factors']['total'], 40)
