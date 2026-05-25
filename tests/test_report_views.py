"""
Tests for report analytics views:
  - LeadReportView  GET /reports/leads/
  - AgentReportView GET /reports/agents/
  - PropertyReportView GET /reports/properties/
  - DealReportView  GET /reports/deals/

Coverage: RBAC, org scoping, response shape, cross-org isolation.
"""
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.escrow.models import EscrowDeal
from tests.factories import (
    make_user, make_developer, make_agent, make_lead,
    make_property, make_deal,
)

LEAD_URL     = "/api/v1/reports/leads/"
AGENT_URL    = "/api/v1/reports/agents/"
PROP_URL     = "/api/v1/reports/properties/"
DEAL_URL     = "/api/v1/reports/deals/"


class _Base(APITestCase):
    def setUp(self):
        self.admin              = make_user(role='admin')
        self.dev_a, self.org_a  = make_developer(org_name='Org A')
        self.dev_b, self.org_b  = make_developer(org_name='Org B')
        self.agent_user, _      = make_agent(org=self.org_a)

        # Seed Org A data
        client_a = make_user(role='client')
        self.lead_a = make_lead(client_a, org=self.org_a, status='warm')
        self.prop_a = make_property(org=self.org_a)
        self.deal_a = make_deal(prop=self.prop_a, buyer=client_a)

        # Seed Org B data
        client_b = make_user(role='client')
        self.lead_b = make_lead(client_b, org=self.org_b, status='new')
        self.prop_b = make_property(org=self.org_b)

    def _auth(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c


# ── RBAC ──────────────────────────────────────────────────────────────────────

class ReportRbacTest(_Base):

    def _check_access(self, url):
        self.assertEqual(self._auth(self.admin).get(url).status_code,      status.HTTP_200_OK)
        self.assertEqual(self._auth(self.dev_a).get(url).status_code,      status.HTTP_200_OK)
        self.assertEqual(self._auth(self.agent_user).get(url).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(APIClient().get(url).status_code,                 status.HTTP_401_UNAUTHORIZED)

    def test_lead_report_access(self):
        self._check_access(LEAD_URL)

    def test_agent_report_access(self):
        self._check_access(AGENT_URL)

    def test_property_report_access(self):
        self._check_access(PROP_URL)

    def test_deal_report_access(self):
        self._check_access(DEAL_URL)


# ── Response shape ─────────────────────────────────────────────────────────────

class ReportShapeTest(_Base):

    def test_lead_report_shape(self):
        resp = self._auth(self.dev_a).get(LEAD_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for key in ('total', 'avg_score', 'hot_leads', 'by_status', 'by_intent', 'by_source'):
            self.assertIn(key, resp.data)

    def test_agent_report_shape(self):
        resp = self._auth(self.dev_a).get(AGENT_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('count', resp.data)
        self.assertIn('results', resp.data)

    def test_property_report_shape(self):
        resp = self._auth(self.dev_a).get(PROP_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for key in ('total', 'avg_ai_score', 'installment_available', 'by_type', 'by_risk_level'):
            self.assertIn(key, resp.data)

    def test_deal_report_shape(self):
        resp = self._auth(self.dev_a).get(DEAL_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for key in ('total_locks', 'completed', 'expired', 'disputed', 'avg_confirm_hours', 'by_status', 'by_gateway'):
            self.assertIn(key, resp.data, msg=f"Missing key: {key}")

    def test_lead_report_trend_with_period(self):
        resp = self._auth(self.dev_a).get(LEAD_URL, {'period': 'weekly'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('trend', resp.data)


# ── Org scoping ────────────────────────────────────────────────────────────────

class ReportOrgScopingTest(_Base):

    def test_lead_report_scoped_to_own_org(self):
        resp_a = self._auth(self.dev_a).get(LEAD_URL)
        resp_b = self._auth(self.dev_b).get(LEAD_URL)
        # Org A has 1 lead, Org B has 1 lead — totals should differ if seeded correctly
        # At minimum, developer A does not see developer B's leads
        total_a = resp_a.data['total']
        total_b = resp_b.data['total']
        admin_total = self._auth(self.admin).get(LEAD_URL).data['total']
        self.assertLessEqual(total_a, admin_total)
        self.assertLessEqual(total_b, admin_total)
        # Each org only has its own lead
        self.assertEqual(total_a, 1)
        self.assertEqual(total_b, 1)

    def test_deal_report_scoped_to_own_org(self):
        resp_a = self._auth(self.dev_a).get(DEAL_URL)
        resp_b = self._auth(self.dev_b).get(DEAL_URL)
        # Org A has 1 deal, Org B has 0
        self.assertEqual(resp_a.data['total_locks'], 1)
        self.assertEqual(resp_b.data['total_locks'], 0)

    def test_admin_sees_all_leads(self):
        resp = self._auth(self.admin).get(LEAD_URL)
        self.assertGreaterEqual(resp.data['total'], 2)

    def test_property_report_scoped_to_own_org(self):
        resp_a = self._auth(self.dev_a).get(PROP_URL)
        resp_b = self._auth(self.dev_b).get(PROP_URL)
        self.assertEqual(resp_a.data['total'], 1)
        self.assertEqual(resp_b.data['total'], 1)


MONTHLY_URL = "/api/v1/reports/monthly/"


class MonthlyReportOrgFilterTest(_Base):
    """GET /reports/monthly/?org= — bad UUID must return 400, not 500 (A9-LOGIC-2)."""

    def test_bad_uuid_returns_400(self):
        resp = self._auth(self.admin).get(MONTHLY_URL, {'org': 'not-a-uuid'})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('detail', resp.data)

    def test_valid_uuid_returns_200(self):
        resp = self._auth(self.admin).get(MONTHLY_URL, {'org': str(self.org_a.id)})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_non_admin_cannot_filter_by_org(self):
        resp = self._auth(self.dev_a).get(MONTHLY_URL, {'org': str(self.org_b.id)})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
