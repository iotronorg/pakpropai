"""
Tests for the monthly org report feature.

Covers: generator content shape, Celery task (create / idempotency / failure
isolation), MonthlyReportListView RBAC, and cross-org isolation.
"""
import datetime
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone

from apps.reports.models import MonthlyReport
from apps.reports.generator import generate_monthly_report_content

from tests.factories import make_developer, make_org, make_user


def _make_period():
    today = datetime.date.today()
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - datetime.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    start_dt = timezone.make_aware(
        timezone.datetime(last_month_start.year, last_month_start.month, 1)
    )
    end_dt = timezone.make_aware(
        timezone.datetime(first_of_this_month.year, first_of_this_month.month, 1)
    )
    return last_month_start, last_month_end, start_dt, end_dt


# ── Generator ─────────────────────────────────────────────────────────────────

class GeneratorContentShapeTests(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()

    def test_generator_returns_required_sections(self):
        _, _, start_dt, end_dt = _make_period()
        result = generate_monthly_report_content(self.org, start_dt, end_dt)
        self.assertIn('leads', result)
        self.assertIn('top_agents', result)
        self.assertIn('deals', result)
        self.assertIn('properties', result)

    def test_leads_section_shape(self):
        _, _, start_dt, end_dt = _make_period()
        leads = generate_monthly_report_content(self.org, start_dt, end_dt)['leads']
        self.assertIn('total', leads)
        self.assertIn('by_status', leads)
        self.assertIn('qualified', leads)
        self.assertIn('conversion_rate', leads)
        self.assertIn('avg_score', leads)

    def test_top_agents_is_list(self):
        _, _, start_dt, end_dt = _make_period()
        top_agents = generate_monthly_report_content(self.org, start_dt, end_dt)['top_agents']
        self.assertIsInstance(top_agents, list)

    def test_deals_section_shape(self):
        _, _, start_dt, end_dt = _make_period()
        deals = generate_monthly_report_content(self.org, start_dt, end_dt)['deals']
        for key in ('total', 'completed', 'expired', 'disputed'):
            self.assertIn(key, deals)

    def test_properties_section_shape(self):
        _, _, start_dt, end_dt = _make_period()
        props = generate_monthly_report_content(self.org, start_dt, end_dt)['properties']
        self.assertIn('new_listings', props)
        self.assertIn('avg_ai_score', props)
        self.assertIn('by_type', props)

    def test_generator_empty_org_returns_zeros(self):
        _, _, start_dt, end_dt = _make_period()
        result = generate_monthly_report_content(self.org, start_dt, end_dt)
        self.assertEqual(result['leads']['total'], 0)
        self.assertEqual(result['deals']['total'], 0)
        self.assertEqual(result['properties']['new_listings'], 0)


# ── Celery Task ───────────────────────────────────────────────────────────────

class MonthlyReportTaskTests(TestCase):

    def setUp(self):
        self.dev1, self.org1 = make_developer(org_name='Org One')
        self.dev2, self.org2 = make_developer(org_name='Org Two')

    def _run_task(self):
        """Run task with Cloudinary and ReportLab mocked out."""
        with patch('apps.reports.tasks._upload_monthly_pdf', return_value='https://cdn.example.com/report.pdf'), \
             patch('apps.reports.tasks._build_monthly_pdf', return_value=b'%PDF-mock'):
            from apps.reports.tasks import generate_monthly_reports
            return generate_monthly_reports()

    def test_task_creates_one_report_per_org(self):
        self._run_task()
        self.assertEqual(MonthlyReport.objects.filter(organization=self.org1).count(), 1)
        self.assertEqual(MonthlyReport.objects.filter(organization=self.org2).count(), 1)

    def test_task_sets_status_ready(self):
        self._run_task()
        report = MonthlyReport.objects.get(organization=self.org1)
        self.assertEqual(report.status, MonthlyReport.Status.READY)
        self.assertIsNotNone(report.pdf_url)
        self.assertIsNotNone(report.ready_at)

    def test_task_idempotent_does_not_duplicate(self):
        self._run_task()
        self._run_task()
        self.assertEqual(MonthlyReport.objects.filter(organization=self.org1).count(), 1)

    def test_task_continues_after_one_org_failure(self):
        """If one org's PDF generation fails, the others still succeed."""
        call_count = [0]

        def mock_build(org, content, label):
            call_count[0] += 1
            if org == self.org1:
                raise RuntimeError("simulated PDF failure")
            return b'%PDF-mock'

        with patch('apps.reports.tasks._upload_monthly_pdf', return_value='https://cdn.example.com/r.pdf'), \
             patch('apps.reports.tasks._build_monthly_pdf', side_effect=mock_build):
            from apps.reports.tasks import generate_monthly_reports
            result = generate_monthly_reports()

        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['succeeded'], 1)

        failed_report = MonthlyReport.objects.get(organization=self.org1)
        ok_report     = MonthlyReport.objects.get(organization=self.org2)
        self.assertEqual(failed_report.status, MonthlyReport.Status.FAILED)
        self.assertEqual(ok_report.status,     MonthlyReport.Status.READY)

    def test_task_skips_inactive_orgs(self):
        self.org2.is_active = False
        self.org2.save()
        self._run_task()
        self.assertFalse(MonthlyReport.objects.filter(organization=self.org2).exists())


# ── API — MonthlyReportListView ───────────────────────────────────────────────

class MonthlyReportListViewTests(TestCase):

    def setUp(self):
        self.dev1, self.org1 = make_developer(org_name='Org A')
        self.dev2, self.org2 = make_developer(org_name='Org B')
        self.admin            = make_user(role='admin')
        self.agent            = make_user(role='agent')

        period_start = datetime.date(2026, 4, 1)
        period_end   = datetime.date(2026, 4, 30)

        self.report1 = MonthlyReport.objects.create(
            organization=self.org1,
            period_start=period_start,
            period_end=period_end,
            status=MonthlyReport.Status.READY,
            pdf_url='https://cdn.example.com/r1.pdf',
        )
        self.report2 = MonthlyReport.objects.create(
            organization=self.org2,
            period_start=period_start,
            period_end=period_end,
            status=MonthlyReport.Status.READY,
            pdf_url='https://cdn.example.com/r2.pdf',
        )

    def _get(self, user, params=''):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=user)
        return client.get(f'/api/v1/reports/monthly/{params}')

    def test_developer_sees_own_org_only(self):
        response = self._get(self.dev1)
        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in response.data]
        self.assertIn(str(self.report1.id), ids)
        self.assertNotIn(str(self.report2.id), ids)

    def test_admin_sees_all_orgs(self):
        response = self._get(self.admin)
        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in response.data]
        self.assertIn(str(self.report1.id), ids)
        self.assertIn(str(self.report2.id), ids)

    def test_admin_can_filter_by_org(self):
        response = self._get(self.admin, params=f'?org={self.org1.id}')
        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in response.data]
        self.assertIn(str(self.report1.id), ids)
        self.assertNotIn(str(self.report2.id), ids)

    def test_agent_gets_403(self):
        response = self._get(self.agent)
        self.assertEqual(response.status_code, 403)

    def test_response_fields_present(self):
        response = self._get(self.dev1)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data) > 0)
        r = response.data[0]
        for field in ('id', 'period_start', 'period_end', 'status', 'pdf_url', 'created_at'):
            self.assertIn(field, r)

    def test_cross_org_isolation(self):
        """Org B developer cannot see Org A's reports."""
        response = self._get(self.dev2)
        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in response.data]
        self.assertNotIn(str(self.report1.id), ids)
        self.assertIn(str(self.report2.id), ids)
