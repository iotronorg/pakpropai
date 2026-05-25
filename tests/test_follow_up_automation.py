"""
Tests for follow-up automation tasks.
Covers: mark_stale_leads, _send_client_reengagement (flag gate + WA call),
        send_stale_lead_reminders (dedup window), send_appointment_reminders.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.leads.models import Lead, LeadActivity, Appointment
from apps.properties.models import Property
from tests.factories import make_user, make_developer, make_agent, make_lead

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_EAGER  = {'CELERY_TASK_ALWAYS_EAGER': True}


def _stale_lead(org, days_ago=10):
    user = make_user(role='client')
    lead = Lead.objects.create(
        user=user,
        organization=org,
        status=Lead.Status.WARM,
        last_contacted_at=timezone.now() - timedelta(days=days_ago),
    )
    return lead


@override_settings(CACHES=_LOCMEM, **_EAGER)
class MarkStaleLeadsTests(TestCase):

    def setUp(self):
        _, self.org = make_developer()

    def test_marks_warm_lead_cold_after_7_days(self):
        lead = _stale_lead(self.org, days_ago=8)
        from apps.leads.tasks import mark_stale_leads
        count = mark_stale_leads()
        self.assertEqual(count, 1)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.COLD)

    def test_creates_activity_record(self):
        lead = _stale_lead(self.org, days_ago=8)
        from apps.leads.tasks import mark_stale_leads
        mark_stale_leads()
        self.assertTrue(
            LeadActivity.objects.filter(lead=lead, action=LeadActivity.ActionType.STATUS).exists()
        )

    def test_does_not_mark_fresh_lead(self):
        lead = _stale_lead(self.org, days_ago=3)
        from apps.leads.tasks import mark_stale_leads
        count = mark_stale_leads()
        self.assertEqual(count, 0)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.WARM)

    def test_does_not_re_mark_already_cold_lead(self):
        lead = _stale_lead(self.org, days_ago=10)
        lead.status = Lead.Status.COLD
        lead.save()
        from apps.leads.tasks import mark_stale_leads
        count = mark_stale_leads()
        self.assertEqual(count, 0)


@override_settings(CACHES=_LOCMEM, **_EAGER)
class ClientReengagementTests(TestCase):

    def setUp(self):
        _, self.org = make_developer()
        self.lead = _stale_lead(self.org, days_ago=8)

    @override_settings(REALTRON_FEATURE_FOLLOW_UP='false')
    @patch('apps.leads.tasks.get_wa_client')
    def test_skipped_when_flag_off(self, mock_factory):
        from django.core.cache import cache
        from apps.config.models import SystemConfig
        SystemConfig.objects.filter(key='feature_follow_up_automation').delete()
        cache.delete('sysconfig:feature_follow_up_automation')

        from apps.leads.tasks import _send_client_reengagement
        _send_client_reengagement(self.lead)
        mock_factory.assert_not_called()

    @patch('apps.leads.tasks.get_wa_client')
    def test_sends_when_flag_on(self, mock_factory):
        from apps.config.models import SystemConfig
        SystemConfig.objects.update_or_create(
            key='feature_follow_up_automation', defaults={'value': 'true'}
        )
        mock_client = MagicMock()
        mock_factory.return_value = mock_client

        from apps.leads.tasks import _send_client_reengagement
        _send_client_reengagement(self.lead)

        mock_factory.assert_called_once_with(org=self.lead.organization)
        mock_client.send_text.assert_called_once()
        args = mock_client.send_text.call_args[0]
        self.assertEqual(args[0], self.lead.user.phone)

    @patch('apps.leads.tasks.get_wa_client')
    def test_sets_follow_up_sent_at(self, mock_factory):
        from apps.config.models import SystemConfig
        SystemConfig.objects.update_or_create(
            key='feature_follow_up_automation', defaults={'value': 'true'}
        )
        mock_factory.return_value = MagicMock()

        from apps.leads.tasks import _send_client_reengagement
        _send_client_reengagement(self.lead)
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.follow_up_sent_at)

    @patch('apps.leads.tasks.get_wa_client')
    def test_skips_lead_with_no_phone(self, mock_factory):
        from apps.config.models import SystemConfig
        SystemConfig.objects.update_or_create(
            key='feature_follow_up_automation', defaults={'value': 'true'}
        )
        self.lead.user.phone = ''
        self.lead.user.save()

        from apps.leads.tasks import _send_client_reengagement
        _send_client_reengagement(self.lead)
        mock_factory.assert_not_called()


@override_settings(CACHES=_LOCMEM, **_EAGER)
class StaleLeadRemindersTests(TestCase):

    def setUp(self):
        _, self.org = make_developer()
        self.agent_user, self.agent = make_agent(org=self.org)
        self.lead = Lead.objects.create(
            user=make_user(role='client'),
            organization=self.org,
            status=Lead.Status.COLD,
            last_contacted_at=timezone.now() - timedelta(days=15),
            assigned_agent=self.agent,
        )

    @patch('apps.leads.tasks.get_wa_client')
    def test_notifies_agent_for_inactive_lead(self, mock_factory):
        mock_client = MagicMock()
        mock_factory.return_value = mock_client

        from apps.leads.tasks import send_stale_lead_reminders
        count = send_stale_lead_reminders()

        self.assertEqual(count, 1)
        mock_client.send_text.assert_called_once()

    @patch('apps.leads.tasks.get_wa_client')
    def test_sets_follow_up_sent_at_on_lead(self, mock_factory):
        mock_factory.return_value = MagicMock()
        from apps.leads.tasks import send_stale_lead_reminders
        send_stale_lead_reminders()
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.follow_up_sent_at)

    @patch('apps.leads.tasks.get_wa_client')
    def test_skips_recently_reminded_lead(self, mock_factory):
        mock_factory.return_value = MagicMock()
        self.lead.follow_up_sent_at = timezone.now() - timedelta(days=1)
        self.lead.save()

        from apps.leads.tasks import send_stale_lead_reminders
        count = send_stale_lead_reminders()
        self.assertEqual(count, 0)
        mock_factory.return_value.send_text.assert_not_called()

    @patch('apps.leads.tasks.get_wa_client')
    def test_resends_after_resend_window(self, mock_factory):
        mock_factory.return_value = MagicMock()
        self.lead.follow_up_sent_at = timezone.now() - timedelta(days=4)
        self.lead.save()

        from apps.leads.tasks import send_stale_lead_reminders
        count = send_stale_lead_reminders()
        self.assertEqual(count, 1)

    @patch('apps.leads.tasks.get_wa_client')
    def test_skips_unassigned_leads(self, mock_factory):
        self.lead.assigned_agent = None
        self.lead.save()
        from apps.leads.tasks import send_stale_lead_reminders
        count = send_stale_lead_reminders()
        self.assertEqual(count, 0)
        mock_factory.assert_not_called()


@override_settings(CACHES=_LOCMEM, **_EAGER)
class AppointmentReminderTests(TestCase):

    def setUp(self):
        _, self.org = make_developer()
        self.agent_user, self.agent = make_agent(org=self.org)
        self.client_user = make_user(role='client')
        self.lead = Lead.objects.create(
            user=self.client_user,
            organization=self.org,
            status=Lead.Status.WARM,
        )
        self.appt = Appointment.objects.create(
            lead=self.lead,
            agent=self.agent,
            scheduled_at=timezone.now() + timedelta(minutes=30),
            status=Appointment.Status.SCHEDULED,
        )

    @patch('apps.leads.tasks.get_wa_client')
    def test_sends_reminder_in_window(self, mock_factory):
        mock_client = MagicMock()
        mock_factory.return_value = mock_client

        from apps.leads.tasks import send_appointment_reminders
        count = send_appointment_reminders()

        self.assertEqual(count, 1)
        mock_client.send_text.assert_called_once()
        self.appt.refresh_from_db()
        self.assertIsNotNone(self.appt.reminder_sent_at)

    @patch('apps.leads.tasks.get_wa_client')
    def test_does_not_resend_already_reminded(self, mock_factory):
        self.appt.reminder_sent_at = timezone.now()
        self.appt.save()

        from apps.leads.tasks import send_appointment_reminders
        count = send_appointment_reminders()
        self.assertEqual(count, 0)
        mock_factory.assert_not_called()

    @patch('apps.leads.tasks.get_wa_client')
    def test_skips_appointment_outside_window(self, mock_factory):
        self.appt.scheduled_at = timezone.now() + timedelta(hours=3)
        self.appt.save()

        from apps.leads.tasks import send_appointment_reminders
        count = send_appointment_reminders()
        self.assertEqual(count, 0)
        mock_factory.assert_not_called()

    @patch('apps.leads.tasks.get_wa_client')
    def test_uses_lead_org_for_wa_client(self, mock_factory):
        mock_factory.return_value = MagicMock()
        from apps.leads.tasks import send_appointment_reminders
        send_appointment_reminders()
        mock_factory.assert_called_once_with(org=self.org)
