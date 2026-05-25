"""
AppointmentViewSet tests.

Coverage:
  - List scoping: agent sees own, developer sees org, admin sees all
  - Cross-org isolation: agent from org B cannot read/modify org A appointments
  - Create (happy path, missing required field)
  - confirm   — allowed from scheduled; rejected from confirmed/cancelled/completed
  - cancel    — allowed from scheduled, confirmed, rescheduled; rejected from completed
  - reschedule — validates scheduled_at; rejected from cancelled/completed
  - complete  — allowed from confirmed/scheduled; rejected from cancelled
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.leads.models import Appointment
from tests.factories import make_agent, make_client, make_developer, make_lead, make_org

LIST_URL   = "/api/v1/leads/appointments/"
DETAIL_URL = lambda pk: f"/api/v1/leads/appointments/{pk}/"
ACTION_URL = lambda pk, action: f"/api/v1/leads/appointments/{pk}/{action}/"

_FUTURE = lambda minutes=60: (timezone.now() + timedelta(minutes=minutes)).isoformat()


def _make_appointment(lead, agent, **kwargs):
    kwargs.setdefault("scheduled_at", timezone.now() + timedelta(hours=2))
    kwargs.setdefault("duration_minutes", 60)
    kwargs.setdefault("status", Appointment.Status.SCHEDULED)
    return Appointment.objects.create(lead=lead, agent=agent, **kwargs)


# ── Helpers ────────────────────────────────────────────────────────────────────

class _Base(APITestCase):
    def setUp(self):
        self.org  = make_org("Org A")
        self.org2 = make_org("Org B")

        self.dev_user, _ = make_developer(org=self.org)
        self.agent_user, self.agent = make_agent(org=self.org, name="Alice")
        self.agent2_user, self.agent2 = make_agent(org=self.org2, name="Bob")

        from tests.factories import make_user
        self.admin_user = make_user(role="admin")

        self.client_user  = make_client()
        self.lead         = make_lead(self.client_user, org=self.org)
        self.lead2        = make_lead(make_client(), org=self.org2)

    def _auth(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c


# ── List / scoping ─────────────────────────────────────────────────────────────

class AppointmentListScopingTest(_Base):

    def test_agent_sees_only_own_appointments(self):
        appt1 = _make_appointment(self.lead, self.agent)
        _make_appointment(self.lead2, self.agent2)   # other org, other agent

        resp = self._auth(self.agent_user).get(LIST_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [a["id"] for a in (resp.data.get("results") or resp.data)]
        self.assertIn(str(appt1.pk), ids)
        self.assertEqual(len(ids), 1)

    def test_developer_sees_org_appointments_only(self):
        appt1 = _make_appointment(self.lead, self.agent)
        _make_appointment(self.lead2, self.agent2)   # org B

        resp = self._auth(self.dev_user).get(LIST_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [a["id"] for a in (resp.data.get("results") or resp.data)]
        self.assertIn(str(appt1.pk), ids)
        self.assertEqual(len(ids), 1)

    def test_admin_sees_all_appointments(self):
        _make_appointment(self.lead, self.agent)
        _make_appointment(self.lead2, self.agent2)

        resp = self._auth(self.admin_user).get(LIST_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [a["id"] for a in (resp.data.get("results") or resp.data)]
        self.assertEqual(len(ids), 2)

    def test_unauthenticated_is_rejected(self):
        resp = APIClient().get(LIST_URL)
        self.assertIn(resp.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])


# ── Cross-org isolation ────────────────────────────────────────────────────────

class AppointmentCrossOrgIsolationTest(_Base):

    def test_agent_cannot_read_other_orgs_appointment(self):
        appt = _make_appointment(self.lead2, self.agent2)
        resp = self._auth(self.agent_user).get(DETAIL_URL(appt.pk))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_agent_cannot_confirm_other_orgs_appointment(self):
        appt = _make_appointment(self.lead2, self.agent2)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "confirm"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_developer_cannot_read_other_orgs_appointment(self):
        appt = _make_appointment(self.lead2, self.agent2)
        resp = self._auth(self.dev_user).get(DETAIL_URL(appt.pk))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_developer_cannot_create_appointment_with_foreign_lead(self):
        payload = {
            "lead": self.lead2.pk,          # belongs to org2
            "agent": self.agent.pk,
            "scheduled_at": _FUTURE(90),
            "duration_minutes": 60,
        }
        resp = self._auth(self.dev_user).post(LIST_URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("lead", resp.data)

    def test_developer_cannot_create_appointment_with_foreign_agent(self):
        payload = {
            "lead": self.lead.pk,
            "agent": self.agent2.pk,        # belongs to org2
            "scheduled_at": _FUTURE(90),
            "duration_minutes": 60,
        }
        resp = self._auth(self.dev_user).post(LIST_URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("agent", resp.data)

    def test_agent_cannot_create_appointment_with_foreign_lead(self):
        payload = {
            "lead": self.lead2.pk,          # belongs to org2
            "agent": self.agent.pk,
            "scheduled_at": _FUTURE(90),
            "duration_minutes": 60,
        }
        resp = self._auth(self.agent_user).post(LIST_URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("lead", resp.data)


# ── Create ─────────────────────────────────────────────────────────────────────

class AppointmentCreateTest(_Base):

    def test_agent_can_create_appointment(self):
        payload = {
            "lead": self.lead.pk,
            "agent": self.agent.pk,
            "scheduled_at": _FUTURE(90),
            "duration_minutes": 60,
        }
        resp = self._auth(self.agent_user).post(LIST_URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["status"], "scheduled")

    def test_developer_can_create_appointment(self):
        payload = {
            "lead": self.lead.pk,
            "agent": self.agent.pk,
            "scheduled_at": _FUTURE(90),
            "duration_minutes": 30,
        }
        resp = self._auth(self.dev_user).post(LIST_URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_create_missing_lead_returns_400(self):
        payload = {
            "scheduled_at": _FUTURE(90),
            "duration_minutes": 60,
        }
        resp = self._auth(self.agent_user).post(LIST_URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_missing_scheduled_at_returns_400(self):
        payload = {
            "lead": self.lead.pk,
            "duration_minutes": 60,
        }
        resp = self._auth(self.agent_user).post(LIST_URL, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ── confirm ────────────────────────────────────────────────────────────────────

class AppointmentConfirmTest(_Base):

    def test_confirm_from_scheduled_succeeds(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.SCHEDULED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "confirm"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CONFIRMED)

    def test_confirm_from_already_confirmed_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.CONFIRMED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "confirm"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_from_cancelled_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.CANCELLED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "confirm"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_from_completed_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.COMPLETED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "confirm"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ── cancel ─────────────────────────────────────────────────────────────────────

class AppointmentCancelTest(_Base):

    def test_cancel_from_scheduled_succeeds(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.SCHEDULED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "cancel"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)

    def test_cancel_from_confirmed_succeeds(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.CONFIRMED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "cancel"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_cancel_from_rescheduled_succeeds(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.RESCHEDULED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "cancel"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_cancel_from_completed_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.COMPLETED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "cancel"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancel_from_already_cancelled_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.CANCELLED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "cancel"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ── reschedule ─────────────────────────────────────────────────────────────────

class AppointmentRescheduleTest(_Base):

    def test_reschedule_from_scheduled_succeeds(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.SCHEDULED)
        new_time = _FUTURE(180)
        resp = self._auth(self.agent_user).post(
            ACTION_URL(appt.pk, "reschedule"),
            {"scheduled_at": new_time},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.RESCHEDULED)

    def test_reschedule_from_confirmed_succeeds(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.CONFIRMED)
        resp = self._auth(self.agent_user).post(
            ACTION_URL(appt.pk, "reschedule"),
            {"scheduled_at": _FUTURE(120)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_reschedule_missing_scheduled_at_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.SCHEDULED)
        resp = self._auth(self.agent_user).post(
            ACTION_URL(appt.pk, "reschedule"),
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reschedule_invalid_datetime_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.SCHEDULED)
        resp = self._auth(self.agent_user).post(
            ACTION_URL(appt.pk, "reschedule"),
            {"scheduled_at": "not-a-date"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reschedule_from_cancelled_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.CANCELLED)
        resp = self._auth(self.agent_user).post(
            ACTION_URL(appt.pk, "reschedule"),
            {"scheduled_at": _FUTURE(120)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reschedule_from_completed_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.COMPLETED)
        resp = self._auth(self.agent_user).post(
            ACTION_URL(appt.pk, "reschedule"),
            {"scheduled_at": _FUTURE(120)},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ── complete ───────────────────────────────────────────────────────────────────

class AppointmentCompleteTest(_Base):

    def test_complete_from_confirmed_succeeds(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.CONFIRMED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "complete"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.COMPLETED)

    def test_complete_from_scheduled_succeeds(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.SCHEDULED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "complete"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_complete_from_cancelled_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.CANCELLED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "complete"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_complete_from_already_completed_returns_400(self):
        appt = _make_appointment(self.lead, self.agent, status=Appointment.Status.COMPLETED)
        resp = self._auth(self.agent_user).post(ACTION_URL(appt.pk, "complete"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
