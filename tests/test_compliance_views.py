"""
Tests for compliance API views — AML screening endpoints (9 tests).

Coverage:
  a  developer sees own screenings only (not other org's)
  b  admin sees all orgs' screenings
  c  agent receives 403 on screenings list
  d  export CSV shape + org-scoped content
  e  sanction create admin only — 201 on admin POST
  f  sanction create developer → 403
  g  sanction delete admin only — 204 on delete
  h  cross-org sanction isolation (org A sanctions not in org B's list)
  i  403 on deal-lock initiate with blocked entity (integration)
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.compliance.models import ComplianceSanctionRecord, SanctionScreeningResult
from tests.factories import make_developer, make_user, make_property, make_deal


def _auth(client, user):
    from rest_framework_simplejwt.tokens import RefreshToken
    token = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')


class DeveloperScreeningsOrgScopedTest(TestCase):
    """a — developer sees only own org screenings."""

    def test_a_developer_sees_own_org_only(self):
        dev_user, org = make_developer()
        _, other_org  = make_developer(org_name='Other Org')

        SanctionScreeningResult.objects.create(screened_name='Own Org Result', org=org, status='clear')
        SanctionScreeningResult.objects.create(screened_name='Other Org Result', org=other_org, status='clear')

        client = APIClient()
        _auth(client, dev_user)
        resp = client.get('/api/v1/compliance/screenings/')
        self.assertEqual(resp.status_code, 200)
        names = [r['screened_name'] for r in resp.data]
        self.assertIn('Own Org Result', names)
        self.assertNotIn('Other Org Result', names)


class AdminScreeningsAllOrgsTest(TestCase):
    """b — admin sees all orgs' screenings."""

    def test_b_admin_sees_all_screenings(self):
        _, org_a = make_developer(org_name='Org A')
        _, org_b = make_developer(org_name='Org B')
        admin    = make_user(role='admin')

        SanctionScreeningResult.objects.create(screened_name='Result A', org=org_a, status='clear')
        SanctionScreeningResult.objects.create(screened_name='Result B', org=org_b, status='clear')

        client = APIClient()
        _auth(client, admin)
        resp = client.get('/api/v1/compliance/screenings/')
        self.assertEqual(resp.status_code, 200)
        names = [r['screened_name'] for r in resp.data]
        self.assertIn('Result A', names)
        self.assertIn('Result B', names)


class AgentForbiddenTest(TestCase):
    """c — agent receives 403."""

    def test_c_agent_gets_403(self):
        agent = make_user(role='agent')
        client = APIClient()
        _auth(client, agent)
        resp = client.get('/api/v1/compliance/screenings/')
        self.assertEqual(resp.status_code, 403)


class ExportCSVTest(TestCase):
    """d — export CSV shape + org-scoped content."""

    def test_d_export_csv_returns_csv_and_scoped(self):
        dev_user, org = make_developer()
        _, other_org  = make_developer(org_name='Other')

        SanctionScreeningResult.objects.create(screened_name='Mine', org=org, status='clear')
        SanctionScreeningResult.objects.create(screened_name='NotMine', org=other_org, status='clear')

        client = APIClient()
        _auth(client, dev_user)
        resp = client.get('/api/v1/compliance/screenings/export/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])

        content = resp.content.decode()
        self.assertIn('screening_id', content)  # header row
        self.assertIn('Mine', content)
        self.assertNotIn('NotMine', content)


class SanctionCreateAdminTest(TestCase):
    """e — admin can create a sanction record."""

    def test_e_admin_creates_sanction(self):
        admin = make_user(role='admin')
        client = APIClient()
        _auth(client, admin)
        resp = client.post('/api/v1/compliance/sanctions/', {
            'name': 'New Target',
            'list_source': 'OFAC',
            'risk_level': 'high',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertIn('id', resp.data)
        self.assertTrue(ComplianceSanctionRecord.objects.filter(name='New Target').exists())


class SanctionCreateDeveloperForbiddenTest(TestCase):
    """f — developer POST → 403."""

    def test_f_developer_cannot_create_sanction(self):
        dev_user, _ = make_developer()
        client = APIClient()
        _auth(client, dev_user)
        resp = client.post('/api/v1/compliance/sanctions/', {
            'name': 'Should Fail',
            'list_source': 'LOCAL',
            'risk_level': 'high',
        }, format='json')
        self.assertEqual(resp.status_code, 403)


class SanctionDeleteAdminTest(TestCase):
    """g — admin can deactivate a sanction."""

    def test_g_admin_deactivates_sanction(self):
        admin = make_user(role='admin')
        rec   = ComplianceSanctionRecord.objects.create(name='To Delete', list_source='LOCAL')
        client = APIClient()
        _auth(client, admin)
        resp = client.delete(f'/api/v1/compliance/sanctions/{rec.id}/')
        self.assertEqual(resp.status_code, 204)
        rec.refresh_from_db()
        self.assertFalse(rec.is_active)


class CrossOrgSanctionIsolationTest(TestCase):
    """h — org A screenings not visible to org B developer."""

    def test_h_cross_org_isolation(self):
        dev_a, org_a = make_developer(org_name='Org A')
        dev_b, org_b = make_developer(org_name='Org B')

        SanctionScreeningResult.objects.create(screened_name='Org A Screen', org=org_a, status='flagged')

        client = APIClient()
        _auth(client, dev_b)
        resp = client.get('/api/v1/compliance/screenings/')
        self.assertEqual(resp.status_code, 200)
        names = [r['screened_name'] for r in resp.data]
        self.assertNotIn('Org A Screen', names)


class DealLockBlockedEntityTest(TestCase):
    """i — deal-lock initiate with blocked entity → 403."""

    def test_i_deal_lock_blocked_returns_403(self):
        buyer = make_user(role='client', name='Sanctioned Buyer')
        _, org = make_developer()
        prop  = make_property(org=org)

        ComplianceSanctionRecord.objects.create(name='Sanctioned Buyer', list_source='LOCAL')

        client = APIClient()
        _auth(client, buyer)
        resp = client.post('/api/v1/deals/lock/', {
            'property_id': str(prop.id),
            'token_amount': 25000,
            'payment_gateway': 'manual',
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertIn('detail', resp.data)
        self.assertIn('compliance', resp.data['detail'].lower())
