"""
Tests for follow-up automation tasks.
Covers: mark_stale_leads, _send_client_reengagement (flag gate + WA call),
        send_stale_lead_reminders (dedup window), send_appointment_reminders.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.leads.models import Lead, LeadActivity, Appointment
from apps.properties.models import Property
from tests.factories import make_user, make_developer, make_agent, make_lead

def _queued(prefix):
    """T-1022a: WhatsApp texts are queued in the outbox; delivery runs after commit."""
    import json
    from apps.core.models import OutboxMessage
    rows = list(OutboxMessage.objects.filter(kind='whatsapp.text', idempotency_key__startswith=prefix))
    for r in rows:
        r.data = json.loads(r.payload)
    return rows


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
    def test_skipped_when_flag_off(self):
        from django.core.cache import cache
        from apps.config.models import SystemConfig
        SystemConfig.objects.filter(key='feature_follow_up_automation').delete()
        cache.delete('sysconfig:feature_follow_up_automation')

        from apps.leads.tasks import _send_client_reengagement
        _send_client_reengagement(self.lead)
        self.assertEqual(_queued('reengage:'), [])

    def test_sends_when_flag_on(self):
        from apps.config.models import SystemConfig
        SystemConfig.objects.update_or_create(
            key='feature_follow_up_automation', defaults={'value': 'true'}
        )
        from apps.leads.tasks import _send_client_reengagement
        _send_client_reengagement(self.lead)

        (row,) = _queued(f'reengage:{self.lead.pk}:')
        self.assertEqual(row.organization_id, self.lead.organization.pk)
        self.assertEqual(row.data['phone'], self.lead.user.phone)

    def test_sets_follow_up_sent_at(self):
        from apps.config.models import SystemConfig
        SystemConfig.objects.update_or_create(
            key='feature_follow_up_automation', defaults={'value': 'true'}
        )
        from apps.leads.tasks import _send_client_reengagement
        _send_client_reengagement(self.lead)
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.follow_up_sent_at)

    def test_skips_lead_with_no_phone(self):
        from apps.config.models import SystemConfig
        SystemConfig.objects.update_or_create(
            key='feature_follow_up_automation', defaults={'value': 'true'}
        )
        self.lead.user.phone = ''
        self.lead.user.save()

        from apps.leads.tasks import _send_client_reengagement
        _send_client_reengagement(self.lead)
        self.assertEqual(_queued('reengage:'), [])


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

    def test_notifies_agent_for_inactive_lead(self):
        from apps.leads.tasks import send_stale_lead_reminders
        count = send_stale_lead_reminders()

        self.assertEqual(count, 1)
        (row,) = _queued(f'stale_reminder:{self.agent.pk}:')
        self.assertEqual(row.data['phone'], self.agent_user.phone)
        # a rerun the same day queues nothing new
        self.lead.follow_up_sent_at = None
        self.lead.save()
        send_stale_lead_reminders()
        self.assertEqual(len(_queued('stale_reminder:')), 1)

    def test_sets_follow_up_sent_at_on_lead(self):
        from apps.leads.tasks import send_stale_lead_reminders
        send_stale_lead_reminders()
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.follow_up_sent_at)

    def test_skips_recently_reminded_lead(self):
        self.lead.follow_up_sent_at = timezone.now() - timedelta(days=1)
        self.lead.save()

        from apps.leads.tasks import send_stale_lead_reminders
        count = send_stale_lead_reminders()
        self.assertEqual(count, 0)
        self.assertEqual(_queued('stale_reminder:'), [])

    def test_resends_after_resend_window(self):
        self.lead.follow_up_sent_at = timezone.now() - timedelta(days=4)
        self.lead.save()

        from apps.leads.tasks import send_stale_lead_reminders
        count = send_stale_lead_reminders()
        self.assertEqual(count, 1)

    def test_skips_unassigned_leads(self):
        self.lead.assigned_agent = None
        self.lead.save()
        from apps.leads.tasks import send_stale_lead_reminders
        count = send_stale_lead_reminders()
        self.assertEqual(count, 0)
        self.assertEqual(_queued('stale_reminder:'), [])


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

    def test_sends_reminder_in_window(self):
        from apps.leads.tasks import send_appointment_reminders
        count = send_appointment_reminders()

        self.assertEqual(count, 1)
        (row,) = _queued(f'appt_reminder:{self.appt.pk}:')
        self.assertEqual(row.data['phone'], self.client_user.phone)
        self.assertEqual(row.expires_at, self.appt.scheduled_at)
        self.appt.refresh_from_db()
        self.assertIsNotNone(self.appt.reminder_sent_at)

    def test_does_not_resend_already_reminded(self):
        self.appt.reminder_sent_at = timezone.now()
        self.appt.save()

        from apps.leads.tasks import send_appointment_reminders
        count = send_appointment_reminders()
        self.assertEqual(count, 0)
        self.assertEqual(_queued('appt_reminder:'), [])

    def test_skips_appointment_outside_window(self):
        self.appt.scheduled_at = timezone.now() + timedelta(hours=3)
        self.appt.save()

        from apps.leads.tasks import send_appointment_reminders
        count = send_appointment_reminders()
        self.assertEqual(count, 0)
        self.assertEqual(_queued('appt_reminder:'), [])

    def test_uses_lead_org_for_wa_client(self):
        from apps.leads.tasks import send_appointment_reminders
        send_appointment_reminders()
        (row,) = _queued('appt_reminder:')
        self.assertEqual(row.organization_id, self.org.pk)
