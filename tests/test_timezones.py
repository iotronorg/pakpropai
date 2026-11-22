"""
T-1050c — UTC storage/scheduling, market-local presentation and bucketing.
"""
import json
from datetime import datetime, timedelta, timezone as dt_tz
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.markets.services import (
    format_local, local_day_bounds, market_timezone, org_timezone,
)
from tests.factories import make_developer, make_lead, make_org, make_user

PKT = ZoneInfo('Asia/Karachi')
UTC = dt_tz.utc


class UtcSettingsTest(SimpleTestCase):
    def test_storage_and_scheduler_are_utc(self):
        self.assertEqual(settings.TIME_ZONE, 'UTC')
        self.assertEqual(settings.CELERY_TIMEZONE, 'UTC')
        self.assertTrue(settings.USE_TZ)

    def test_crontabs_keep_their_former_pkt_instants(self):
        beat = settings.CELERY_BEAT_SCHEDULE
        reeng = beat['run-reengagement-worker']['schedule']
        self.assertEqual(sorted(reeng.hour), [1, 7, 13, 19])          # 00/06/12/18 PKT
        aml = beat['sync-aml-sanctions']['schedule']
        self.assertEqual((aml.hour, aml.minute, aml.day_of_week), ({21}, {0}, {0}))  # Mon 02:00 PKT
        monthly = beat['monthly-report-generation']['schedule']
        self.assertEqual((len(monthly.hour), monthly.day_of_month), (24, {1}))


class TimezoneHelpersTest(TestCase):
    def test_market_and_org_zones(self):
        self.assertEqual(market_timezone('PK'), PKT)
        self.assertEqual(market_timezone(None), PKT)            # default market
        self.assertEqual(market_timezone('ZZ'), ZoneInfo('UTC'))
        self.assertEqual(org_timezone(make_org(name='Dubai Co', country='AE')), ZoneInfo('Asia/Dubai'))
        self.assertEqual(org_timezone(None), PKT)

    def test_invalid_zone_falls_back_to_utc(self):
        from apps.markets.services import get_market
        info = get_market('PK')
        with patch('apps.markets.services.get_market', return_value=info.__class__(
                **{**info.__dict__, 'timezone': 'Mars/Olympus'})):
            self.assertEqual(market_timezone('PK'), ZoneInfo('UTC'))

    def test_local_day_bounds(self):
        start, end = local_day_bounds(PKT, datetime(2026, 11, 22).date())
        self.assertEqual(start.astimezone(UTC), datetime(2026, 11, 21, 19, 0, tzinfo=UTC))
        self.assertEqual(end - start, timedelta(days=1))
        # DST day in London is 25 hours
        s, e = local_day_bounds(ZoneInfo('Europe/London'), datetime(2026, 10, 25).date())
        self.assertEqual(e.astimezone(UTC) - s.astimezone(UTC), timedelta(hours=25))

    def test_format_local(self):
        dt = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
        self.assertEqual(format_local(dt, None, '%H:%M'), '15:00')
        self.assertEqual(format_local(None, None, '%H:%M'), '')


class LocalTimeInMessagesTest(TestCase):
    """KI-047: reminder texts showed the stored UTC time."""

    def test_appointment_reminder_uses_market_time(self):
        from apps.core.models import OutboxMessage
        from apps.leads.models import Appointment
        from apps.leads.tasks import send_appointment_reminders
        _, org = make_developer()
        lead = make_lead(make_user(role='client'), org=org)
        appt = Appointment.objects.create(
            lead=lead, scheduled_at=timezone.now() + timedelta(minutes=30),
            status=Appointment.Status.SCHEDULED)
        send_appointment_reminders()
        text = json.loads(OutboxMessage.objects.get(
            idempotency_key__startswith=f'appt_reminder:{appt.pk}:').payload)['text']
        self.assertIn(appt.scheduled_at.astimezone(PKT).strftime('%I:%M %p'), text)
        self.assertNotIn(appt.scheduled_at.astimezone(UTC).strftime('%I:%M %p'), text)


class LocalBucketingTest(TestCase):
    def test_new_today_counts_the_local_day(self):
        from apps.leads.models import Lead
        dev, org = make_developer()
        start, _ = local_day_bounds(PKT)
        inside = make_lead(make_user(role='client'), org=org)
        before = make_lead(make_user(role='client'), org=org)
        Lead.objects.filter(pk=inside.pk).update(created_at=start + timedelta(minutes=1))
        Lead.objects.filter(pk=before.pk).update(created_at=start - timedelta(minutes=1))
        c = APIClient()
        c.force_authenticate(dev)
        r = c.get('/api/v1/leads/stats/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['new_today'], 1)

    def test_monthly_trend_buckets_in_local_time(self):
        from apps.leads.models import Lead
        from apps.reports.views import _get_trend
        _, org = make_developer()
        local_now = timezone.now().astimezone(PKT)
        prev_first = (local_now.replace(day=1) - timedelta(days=1)).replace(day=1)
        # 02:00 PKT on the 1st is still the previous month in UTC
        stamp = datetime(prev_first.year, prev_first.month, 1, 2, 0, tzinfo=PKT)
        lead = make_lead(make_user(role='client'), org=org)
        Lead.objects.filter(pk=lead.pk).update(created_at=stamp)
        qs = Lead.objects.filter(pk=lead.pk)
        self.assertEqual(_get_trend(qs, 'created_at', 'monthly', PKT)[0]['period'], stamp.strftime('%Y-%m'))
        self.assertNotEqual(_get_trend(qs, 'created_at', 'monthly', ZoneInfo('UTC'))[0]['period'],
                            stamp.strftime('%Y-%m'))


class MonthlyReportLocalMonthTest(TestCase):
    def _run(self, now):
        from apps.reports.tasks import generate_monthly_reports
        with patch('apps.reports.tasks._upload_monthly_pdf', return_value='https://cdn/x.pdf'), \
                patch('apps.reports.tasks._build_monthly_pdf', return_value=b'%PDF'), \
                patch('apps.reports.generator.generate_monthly_report_content',
                      side_effect=lambda org, s, e: {'bounds': [s.isoformat(), e.isoformat()]}):
            return generate_monthly_reports(now=now)

    def test_each_org_gets_its_last_complete_local_month(self):
        from datetime import date
        from apps.reports.models import MonthlyReport
        _, pk_org = make_developer(org_name='PK Org')
        us_org = make_org(name='US Org', country='US')
        # 03:00 UTC on 1 Sep: 08:00 PKT (August over) but 23:00 on 31 Aug in New York
        self._run(datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
        pk = MonthlyReport.objects.get(organization=pk_org)
        self.assertEqual(pk.period_start, date(2026, 8, 1))
        self.assertEqual(pk.content['bounds'][0], datetime(2026, 8, 1, tzinfo=PKT).isoformat())
        us = MonthlyReport.objects.get(organization=us_org)
        self.assertEqual(us.period_start, date(2026, 7, 1))  # catch-up of the last complete month
        # 05:00 UTC: New York has reached 1 Sep → August; PK is not duplicated
        self._run(datetime(2026, 9, 1, 5, 0, tzinfo=UTC))
        self.assertTrue(MonthlyReport.objects.filter(organization=us_org, period_start=date(2026, 8, 1)).exists())
        self.assertEqual(MonthlyReport.objects.filter(organization=pk_org).count(), 1)
