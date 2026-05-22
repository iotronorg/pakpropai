"""
Cross-org isolation tests — Task 9 of TEST-SUITE-ALIGN directive.

Verifies that developer and agent users cannot read data belonging to a
different organisation. Each test class sets up two fully independent orgs
(org_a / org_b) with their own developers, agents, leads, properties, deals,
and document scans, then asserts that each developer only ever sees their own
org's records and never the other org's.

Note: use_membership_rbac defaults to 'true', so get_user_org() uses
OrganizationMembership. All helpers create the required membership rows.
"""

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.escrow.models import EscrowDeal
from apps.leads.models import Lead
from apps.organizations.models import Organization
from tests.factories import (
    make_client, make_developer, make_agent,
    make_property, make_lead, make_deal, make_scan, make_org,
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _org(name, slug):
    return make_org(name=name, slug=slug)


def _dev(phone, org):
    user, _ = make_developer(phone=phone, org=org)
    return user


def _agent(phone, org):
    return make_agent(phone=phone, org=org)


def _client(phone):
    return make_client(phone=phone)


def _property(org, title, price=1_000_000):
    return make_property(org=org, title=title, price=price, city='Dubai', location='Downtown')


def _lead(user, org, agent=None):
    return make_lead(user=user, org=org, assigned_agent=agent)


def _deal(prop, buyer, agent=None):
    return make_deal(buyer=buyer, prop=prop, agent=agent)


def _scan(prop, requester):
    return make_scan(prop=prop, requester=requester)


# ── Test classes ───────────────────────────────────────────────────────────────

class LeadIsolationTests(TestCase):
    """Developer of org A must never see org B's leads — list or detail."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_b = APIClient()

        self.org_a = _org('Org Alpha', 'org-alpha')
        self.org_b = _org('Org Beta',  'org-beta')

        self.dev_a = _dev('+920001000001', self.org_a)
        self.dev_b = _dev('+920002000001', self.org_b)

        buyer_a = _client('+920001000002')
        buyer_b = _client('+920002000002')

        _, self.agent_a = _agent('+920001000003', self.org_a)
        _, self.agent_b = _agent('+920002000003', self.org_b)

        self.lead_a = _lead(buyer_a, self.org_a, self.agent_a)
        self.lead_b = _lead(buyer_b, self.org_b, self.agent_b)

        self.client_a.force_authenticate(user=self.dev_a)
        self.client_b.force_authenticate(user=self.dev_b)

    def test_developer_list_contains_only_own_org_leads(self):
        resp = self.client_a.get(reverse('leads-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in resp.data.get('results', resp.data)]
        self.assertIn(str(self.lead_a.pk), ids)
        self.assertNotIn(str(self.lead_b.pk), ids)

    def test_developer_b_list_contains_only_org_b_leads(self):
        resp = self.client_b.get(reverse('leads-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in resp.data.get('results', resp.data)]
        self.assertIn(str(self.lead_b.pk), ids)
        self.assertNotIn(str(self.lead_a.pk), ids)

    def test_developer_cannot_retrieve_other_org_lead_detail(self):
        resp = self.client_a.get(
            reverse('leads-detail', kwargs={'pk': str(self.lead_b.pk)})
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_agent_sees_only_assigned_leads_not_other_org(self):
        """Agent A (assigned to lead_a) must not see lead_b."""
        agent_client = APIClient()
        agent_client.force_authenticate(user=self.agent_a.user)
        resp = agent_client.get(reverse('leads-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in resp.data.get('results', resp.data)]
        self.assertIn(str(self.lead_a.pk), ids)
        self.assertNotIn(str(self.lead_b.pk), ids)


class PropertyIsolationTests(TestCase):
    """Developer of org A must never see org B's property inventory."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_b = APIClient()

        self.org_a = _org('PropOrg Alpha', 'prop-org-alpha')
        self.org_b = _org('PropOrg Beta',  'prop-org-beta')

        self.dev_a = _dev('+920003000001', self.org_a)
        self.dev_b = _dev('+920004000001', self.org_b)

        self.prop_a = _property(self.org_a, 'Alpha Tower')
        self.prop_b = _property(self.org_b, 'Beta Villa')

        self.client_a.force_authenticate(user=self.dev_a)
        self.client_b.force_authenticate(user=self.dev_b)

    def test_developer_list_contains_only_own_org_properties(self):
        resp = self.client_a.get(reverse('property-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [str(r['id']) for r in resp.data.get('results', resp.data)]
        self.assertIn(str(self.prop_a.pk), ids)
        self.assertNotIn(str(self.prop_b.pk), ids)

    def test_developer_b_list_contains_only_org_b_properties(self):
        resp = self.client_b.get(reverse('property-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [str(r['id']) for r in resp.data.get('results', resp.data)]
        self.assertIn(str(self.prop_b.pk), ids)
        self.assertNotIn(str(self.prop_a.pk), ids)

    def test_developer_cannot_retrieve_other_org_property_detail(self):
        resp = self.client_a.get(
            reverse('property-detail', kwargs={'pk': str(self.prop_b.pk)})
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class DealIsolationTests(TestCase):
    """Developer of org A must never see org B's escrow deals."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_b = APIClient()

        self.org_a = _org('DealOrg Alpha', 'deal-org-alpha')
        self.org_b = _org('DealOrg Beta',  'deal-org-beta')

        self.dev_a = _dev('+920005000001', self.org_a)
        self.dev_b = _dev('+920006000001', self.org_b)

        buyer_a = _client('+920005000002')
        buyer_b = _client('+920006000002')

        _, self.agent_a = _agent('+920005000003', self.org_a)
        _, self.agent_b = _agent('+920006000003', self.org_b)

        self.prop_a = _property(self.org_a, 'Deal Prop Alpha')
        self.prop_b = _property(self.org_b, 'Deal Prop Beta')

        self.deal_a = _deal(self.prop_a, buyer_a, self.agent_a)
        self.deal_b = _deal(self.prop_b, buyer_b, self.agent_b)

        self.client_a.force_authenticate(user=self.dev_a)
        self.client_b.force_authenticate(user=self.dev_b)

    def test_developer_deal_list_contains_only_own_org_deals(self):
        resp = self.client_a.get(reverse('deal-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [str(r['id']) for r in resp.data.get('results', resp.data)]
        self.assertIn(str(self.deal_a.pk), ids)
        self.assertNotIn(str(self.deal_b.pk), ids)

    def test_developer_b_deal_list_contains_only_org_b_deals(self):
        resp = self.client_b.get(reverse('deal-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [str(r['id']) for r in resp.data.get('results', resp.data)]
        self.assertIn(str(self.deal_b.pk), ids)
        self.assertNotIn(str(self.deal_a.pk), ids)

    def test_developer_cannot_retrieve_other_org_deal_detail(self):
        resp = self.client_a.get(
            reverse('deal-detail', kwargs={'pk': str(self.deal_b.pk)})
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_agent_sees_only_own_assigned_deals(self):
        """Agent A only sees deals where they are the assigned agent."""
        agent_client = APIClient()
        agent_client.force_authenticate(user=self.agent_a.user)
        resp = agent_client.get(reverse('deal-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [str(r['id']) for r in resp.data.get('results', resp.data)]
        self.assertIn(str(self.deal_a.pk), ids)
        self.assertNotIn(str(self.deal_b.pk), ids)


class AgentIsolationTests(TestCase):
    """Developer of org A must never see org B's agent roster."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_b = APIClient()

        self.org_a = _org('AgentOrg Alpha', 'agent-org-alpha')
        self.org_b = _org('AgentOrg Beta',  'agent-org-beta')

        self.dev_a = _dev('+920007000001', self.org_a)
        self.dev_b = _dev('+920008000001', self.org_b)

        _, self.agent_a = _agent('+920007000002', self.org_a)
        _, self.agent_b = _agent('+920008000002', self.org_b)

        self.client_a.force_authenticate(user=self.dev_a)
        self.client_b.force_authenticate(user=self.dev_b)

    def test_developer_agent_list_contains_only_own_org_agents(self):
        resp = self.client_a.get(reverse('agents-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get('results', resp.data)
        ids = [r['id'] for r in results]
        self.assertIn(self.agent_a.pk, ids)
        self.assertNotIn(self.agent_b.pk, ids)

    def test_developer_b_agent_list_contains_only_org_b_agents(self):
        resp = self.client_b.get(reverse('agents-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get('results', resp.data)
        ids = [r['id'] for r in results]
        self.assertIn(self.agent_b.pk, ids)
        self.assertNotIn(self.agent_a.pk, ids)

    def test_developer_cannot_manage_other_org_agent(self):
        """PATCH on an agent from another org must be rejected."""
        resp = self.client_a.patch(
            reverse('agents-admin-detail', kwargs={'pk': self.agent_b.pk}),
            data={'name': 'Hijacked'},
            format='json',
        )
        self.assertIn(resp.status_code, [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ])


class DocumentScanIsolationTests(TestCase):
    """Developer of org A must never see org B's document scans."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_b = APIClient()

        self.org_a = _org('ScanOrg Alpha', 'scan-org-alpha')
        self.org_b = _org('ScanOrg Beta',  'scan-org-beta')

        self.dev_a = _dev('+920009000001', self.org_a)
        self.dev_b = _dev('+920010000001', self.org_b)

        requester_a = _client('+920009000002')
        requester_b = _client('+920010000002')

        prop_a = _property(self.org_a, 'Scan Prop Alpha')
        prop_b = _property(self.org_b, 'Scan Prop Beta')

        self.scan_a, _ = _scan(prop_a, requester_a)
        self.scan_b, _ = _scan(prop_b, requester_b)

        self.client_a.force_authenticate(user=self.dev_a)
        self.client_b.force_authenticate(user=self.dev_b)

    def test_developer_scan_list_contains_only_own_org_scans(self):
        resp = self.client_a.get(reverse('document-scan-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in resp.data.get('results', [])]
        self.assertIn(self.scan_a.pk, ids)
        self.assertNotIn(self.scan_b.pk, ids)

    def test_developer_b_scan_list_contains_only_org_b_scans(self):
        resp = self.client_b.get(reverse('document-scan-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in resp.data.get('results', [])]
        self.assertIn(self.scan_b.pk, ids)
        self.assertNotIn(self.scan_a.pk, ids)

    def test_developer_cannot_retrieve_other_org_scan_detail(self):
        resp = self.client_a.get(
            reverse('document-scan-detail', kwargs={'pk': self.scan_b.pk})
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
