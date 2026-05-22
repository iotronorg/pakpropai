"""
Celery task tests — Task 6 of TEST-SUITE-PHASE2.

All tasks execute synchronously via CELERY_TASK_ALWAYS_EAGER=True (test settings).
External I/O (WhatsApp, Cloudinary) is mocked to prevent real network calls.

Coverage:
  - cleanup_expired_otps       (users)       — deletes expired, skips fresh, returns count
  - mark_stale_leads           (leads)       — marks COLD, creates LeadActivity, skips fresh
  - send_appointment_reminders (leads)       — sends reminder, sets reminder_sent_at, skips already-reminded
  - send_whatsapp_async        (notifications) — SENT / 24h-FAILED / error-FAILED+retry / missing ID noop
  - retry_failed_notifications (notifications) — re-queues FAILED, skips 24h-window, skips old
  - execute_data_deletion      (compliance)  — anonymizes PII, marks completed
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from tests.factories import make_developer, make_agent, make_lead, make_user

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


# ── cleanup_expired_otps ──────────────────────────────────────────────────────

class CleanupExpiredOTPsTest(TestCase):

    def test_deletes_all_expired_otps_and_returns_count(self):
        from apps.users.models import OTPCode
        from apps.users.tasks import cleanup_expired_otps
        OTPCode.objects.create(phone='+923001111001', code='000001',
                               expires_at=timezone.now() - timedelta(minutes=5))
        OTPCode.objects.create(phone='+923001111002', code='000002',
                               expires_at=timezone.now() - timedelta(hours=1))
        deleted = cleanup_expired_otps()
        self.assertEqual(deleted, 2)
        self.assertEqual(OTPCode.objects.count(), 0)

    def test_skips_non_expired_otps(self):
        from apps.users.models import OTPCode
        from apps.users.tasks import cleanup_expired_otps
        OTPCode.objects.create(phone='+923001111003', code='000003',
                               expires_at=timezone.now() + timedelta(minutes=10))
        deleted = cleanup_expired_otps()
        self.assertEqual(deleted, 0)
        self.assertEqual(OTPCode.objects.count(), 1)

    def test_returns_zero_when_nothing_to_delete(self):
        from apps.users.tasks import cleanup_expired_otps
        self.assertEqual(cleanup_expired_otps(), 0)


# ── mark_stale_leads ──────────────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class MarkStaleLeadsTest(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()
        self.client = make_user()

    @patch('apps.whatsapp.client.WhatsAppClient.send_text')
    def test_marks_stale_lead_cold(self, _wa):
        from apps.leads.models import Lead
        from apps.leads.tasks import mark_stale_leads
        lead = make_lead(
            self.client, org=self.org,
            status=Lead.Status.NEW,
            last_contacted_at=timezone.now() - timedelta(days=8),
        )
        count = mark_stale_leads()
        self.assertGreaterEqual(count, 1)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.COLD)

    @patch('apps.whatsapp.client.WhatsAppClient.send_text')
    def test_creates_lead_activity_for_stale_lead(self, _wa):
        from apps.leads.models import Lead, LeadActivity
        from apps.leads.tasks import mark_stale_leads
        lead = make_lead(
            self.client, org=self.org,
            status=Lead.Status.NEW,
            last_contacted_at=timezone.now() - timedelta(days=10),
        )
        mark_stale_leads()
        activity = LeadActivity.objects.filter(
            lead=lead, action=LeadActivity.ActionType.STATUS
        ).first()
        self.assertIsNotNone(activity)
        self.assertIn('COLD', activity.notes.upper())

    @patch('apps.whatsapp.client.WhatsAppClient.send_text')
    def test_skips_recently_contacted_leads(self, _wa):
        from apps.leads.models import Lead
        from apps.leads.tasks import mark_stale_leads
        lead = make_lead(
            self.client, org=self.org,
            status=Lead.Status.NEW,
            last_contacted_at=timezone.now() - timedelta(days=2),
        )
        mark_stale_leads()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)

    @patch('apps.whatsapp.client.WhatsAppClient.send_text')
    def test_skips_leads_with_no_contact_date_but_fresh_created(self, _wa):
        """Leads with last_contacted_at=None are not touched (cutoff filters on that field)."""
        from apps.leads.models import Lead
        from apps.leads.tasks import mark_stale_leads
        lead = make_lead(
            self.client, org=self.org,
            status=Lead.Status.NEW,
            last_contacted_at=None,
        )
        mark_stale_leads()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.NEW)


# ── send_appointment_reminders ────────────────────────────────────────────────

class SendAppointmentRemindersTest(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()
        self.client = make_user()
        self.lead = make_lead(self.client, org=self.org)

    @patch('apps.whatsapp.client.WhatsAppClient.send_text')
    def test_sends_reminder_for_appointment_within_window(self, mock_wa):
        from apps.leads.models import Appointment
        from apps.leads.tasks import send_appointment_reminders
        appt = Appointment.objects.create(
            lead=self.lead,
            scheduled_at=timezone.now() + timedelta(minutes=30),
            status=Appointment.Status.SCHEDULED,
            reminder_sent_at=None,
        )
        count = send_appointment_reminders()
        self.assertGreaterEqual(count, 1)
        appt.refresh_from_db()
        self.assertIsNotNone(appt.reminder_sent_at)
        mock_wa.assert_called()

    @patch('apps.whatsapp.client.WhatsAppClient.send_text')
    def test_skips_already_reminded_appointment(self, mock_wa):
        from apps.leads.models import Appointment
        from apps.leads.tasks import send_appointment_reminders
        Appointment.objects.create(
            lead=self.lead,
            scheduled_at=timezone.now() + timedelta(minutes=30),
            status=Appointment.Status.SCHEDULED,
            reminder_sent_at=timezone.now() - timedelta(hours=1),
        )
        count = send_appointment_reminders()
        self.assertEqual(count, 0)
        mock_wa.assert_not_called()

    @patch('apps.whatsapp.client.WhatsAppClient.send_text')
    def test_skips_appointments_outside_window(self, mock_wa):
        from apps.leads.models import Appointment
        from apps.leads.tasks import send_appointment_reminders
        Appointment.objects.create(
            lead=self.lead,
            scheduled_at=timezone.now() + timedelta(hours=5),  # too far out
            status=Appointment.Status.SCHEDULED,
            reminder_sent_at=None,
        )
        count = send_appointment_reminders()
        self.assertEqual(count, 0)


# ── send_whatsapp_async ───────────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class SendWhatsAppAsyncTest(TestCase):

    def setUp(self):
        self.user = make_user()

    def _make_notification(self):
        from apps.notifications.models import Notification
        return Notification.objects.create(
            user=self.user,
            message='Test message',
            channel=Notification.Channel.WHATSAPP,
            status=Notification.Status.PENDING,
        )

    @patch('apps.whatsapp.client.WhatsAppClient.send_text',
           return_value={'messages': [{'id': 'wa_abc123'}]})
    def test_marks_sent_on_successful_delivery(self, _wa):
        from apps.notifications.tasks import send_whatsapp_async
        n = self._make_notification()
        send_whatsapp_async.delay(str(n.id))
        n.refresh_from_db()
        self.assertEqual(n.status, 'sent')
        self.assertEqual(n.wa_message_id, 'wa_abc123')
        self.assertIsNotNone(n.sent_at)

    @patch('apps.whatsapp.client.WhatsAppClient.send_text',
           side_effect=ValueError('24h messaging window expired'))
    def test_marks_failed_on_24h_window_error_without_retry(self, _wa):
        from apps.notifications.tasks import send_whatsapp_async
        n = self._make_notification()
        send_whatsapp_async.delay(str(n.id))
        n.refresh_from_db()
        self.assertEqual(n.status, 'failed')
        self.assertIn('24h', n.error)

    @patch('apps.whatsapp.client.WhatsAppClient.send_text',
           side_effect=Exception('network timeout'))
    def test_marks_failed_on_general_error(self, _wa):
        from apps.notifications.tasks import send_whatsapp_async
        n = self._make_notification()
        try:
            send_whatsapp_async.delay(str(n.id))
        except Exception:
            pass
        n.refresh_from_db()
        self.assertEqual(n.status, 'failed')

    def test_noop_for_nonexistent_notification_id(self):
        from apps.notifications.tasks import send_whatsapp_async
        import uuid
        result = send_whatsapp_async.delay(str(uuid.uuid4()))
        self.assertIsNone(result.get())


# ── retry_failed_notifications ────────────────────────────────────────────────

class RetryFailedNotificationsTest(TestCase):

    def setUp(self):
        self.user = make_user()

    def _make_failed(self, error='generic error'):
        from apps.notifications.models import Notification
        return Notification.objects.create(
            user=self.user,
            message='failed msg',
            status=Notification.Status.FAILED,
            error=error,
        )

    @patch('apps.notifications.tasks.send_whatsapp_async.delay')
    def test_requeues_failed_notification(self, mock_delay):
        from apps.notifications.tasks import retry_failed_notifications
        n = self._make_failed()
        count = retry_failed_notifications()
        self.assertEqual(count, 1)
        n.refresh_from_db()
        self.assertEqual(n.status, 'pending')
        mock_delay.assert_called_once_with(str(n.id))

    @patch('apps.notifications.tasks.send_whatsapp_async.delay')
    def test_skips_24h_window_failures(self, mock_delay):
        from apps.notifications.tasks import retry_failed_notifications
        self._make_failed(error='24h messaging window expired')
        count = retry_failed_notifications()
        self.assertEqual(count, 0)
        mock_delay.assert_not_called()

    @patch('apps.notifications.tasks.send_whatsapp_async.delay')
    def test_skips_notifications_older_than_48h(self, mock_delay):
        from apps.notifications.models import Notification
        from apps.notifications.tasks import retry_failed_notifications
        n = self._make_failed()
        Notification.objects.filter(pk=n.pk).update(
            created_at=timezone.now() - timedelta(hours=50)
        )
        count = retry_failed_notifications()
        self.assertEqual(count, 0)
        mock_delay.assert_not_called()

    @patch('apps.notifications.tasks.send_whatsapp_async.delay')
    def test_skips_preferences_blocked_failures(self, mock_delay):
        from apps.notifications.tasks import retry_failed_notifications
        self._make_failed(error='Blocked by user notification preferences')
        count = retry_failed_notifications()
        self.assertEqual(count, 0)
        mock_delay.assert_not_called()


# ── execute_data_deletion ─────────────────────────────────────────────────────

class ExecuteDataDeletionTest(TestCase):

    def setUp(self):
        self.user = make_user()
        self.user.name  = 'Real Name'
        self.user.email = 'real@example.com'
        self.user.save()

    def _make_deletion_request(self):
        from apps.compliance.models import DataDeletionRequest
        return DataDeletionRequest.objects.create(user=self.user, status='pending')

    def test_marks_request_completed_with_timestamp(self):
        from apps.compliance.tasks import execute_data_deletion
        req = self._make_deletion_request()
        execute_data_deletion(str(req.id))
        req.refresh_from_db()
        self.assertEqual(req.status, 'completed')
        self.assertIsNotNone(req.completed_at)

    def test_anonymizes_user_name(self):
        from apps.compliance.tasks import execute_data_deletion
        req = self._make_deletion_request()
        execute_data_deletion(str(req.id))
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, '[REDACTED]')

    def test_clears_user_email(self):
        from apps.compliance.tasks import execute_data_deletion
        req = self._make_deletion_request()
        execute_data_deletion(str(req.id))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')

    def test_redacts_lead_notes(self):
        from apps.compliance.tasks import execute_data_deletion
        from apps.leads.models import Lead
        dev, org = make_developer()
        lead = make_lead(self.user, org=org, notes='Sensitive info here')
        req = self._make_deletion_request()
        execute_data_deletion(str(req.id))
        lead.refresh_from_db()
        self.assertEqual(lead.notes, '[REDACTED]')
