"""
T-1090d / ADR-018 — Idempotency-Key on create POSTs.

Covers:
  * end to end on appointment booking: retry with the same key creates one
    row and replays the response; no key keeps the old behaviour
  * key reuse with a different body → 422; per-user scoping; bad key → 400
  * only 2xx outcomes are stored; errors and exceptions free the key
  * in-flight → 409, stale lease taken over, expired record frees the key
  * stored body encrypted at rest; purge task; OpenAPI header on all targets
"""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone
from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.core.idempotency import LEASE, idempotent, purge_expired
from apps.core.models import IdempotencyRecord
from apps.leads.models import Appointment
from apps.core.tasks import purge_idempotency_records
from tests.factories import make_agent, make_client, make_developer, make_lead, make_org, make_user

APPTS = '/api/v1/leads/appointments/'


class AppointmentIdempotencyTest(TestCase):

    def setUp(self):
        org = make_org('Org A')
        self.dev_user, _ = make_developer(org=org)
        _, self.agent = make_agent(org=org, name='Alice')
        self.lead = make_lead(make_client(), org=org)
        self.api = APIClient()
        self.api.force_authenticate(self.dev_user)

    def _payload(self, minutes=90):
        return {
            'lead': self.lead.pk, 'agent': self.agent.pk, 'duration_minutes': 60,
            'scheduled_at': (timezone.now() + timedelta(minutes=minutes)).replace(microsecond=0).isoformat(),
        }

    def _post(self, payload, key=None, api=None):
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key is not None else {}
        return (api or self.api).post(APPTS, payload, format='json', **headers)

    def test_without_key_each_post_creates(self):
        payload = self._payload()
        self.assertEqual(self._post(payload).status_code, 201)
        self.assertEqual(self._post(payload).status_code, 201)
        self.assertEqual(Appointment.objects.count(), 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)

    def test_retry_with_same_key_replays_and_creates_once(self):
        payload = self._payload()
        first = self._post(payload, key='book-7f3a9c1e')
        second = self._post(payload, key='book-7f3a9c1e')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()['id'], first.json()['id'])
        self.assertEqual(second['Idempotent-Replayed'], 'true')
        self.assertFalse(first.has_header('Idempotent-Replayed'))
        self.assertEqual(Appointment.objects.count(), 1)

    def test_same_key_different_body_is_422(self):
        self._post(self._payload(90), key='book-1234')
        resp = self._post(self._payload(120), key='book-1234')
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()['error_info']['code'], 'idempotency_key_reused')
        self.assertEqual(Appointment.objects.count(), 1)

    def test_keys_are_scoped_per_user(self):
        other_dev, _ = make_developer(org=make_org('Org B'))
        other = APIClient()
        other.force_authenticate(other_dev)
        self._post(self._payload(), key='shared-key-1')
        # Same key from another user is a fresh request (it fails validation on
        # the foreign lead — it must not replay the first user's appointment).
        resp = self._post(self._payload(), key='shared-key-1', api=other)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.has_header('Idempotent-Replayed'))

    def test_invalid_key_is_400(self):
        for bad in ('has space', 'x' * 256, 'naïve'):
            with self.subTest(bad=bad):
                resp = self._post(self._payload(), key=bad)
                self.assertEqual(resp.status_code, 400)
                self.assertIn('Idempotency-Key', resp.json()['error_info']['fields'])
        self.assertEqual(Appointment.objects.count(), 0)

    def test_validation_error_frees_key_for_corrected_retry(self):
        bad = {**self._payload(), 'lead': 999999}
        self.assertEqual(self._post(bad, key='booking-fix-1').status_code, 400)
        self.assertFalse(IdempotencyRecord.objects.exists())
        fixed = self._post(self._payload(), key='booking-fix-1')       # same key, corrected body
        self.assertEqual(fixed.status_code, 201)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_stored_response_is_encrypted_at_rest(self):
        resp = self._post(self._payload(), key='enc-check-1')
        with connection.cursor() as cur:
            cur.execute('SELECT response_body FROM idempotency_records')
            raw = cur.fetchone()[0]
        self.assertNotIn(resp.json()['id'], raw)
        self.assertTrue(raw.startswith('enc'))


class _Probe(APIView):
    """Minimal view: the handler's outcome is set by the test."""
    outcome = None
    calls = 0

    @idempotent
    def post(self, request):
        type(self).calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return Response({'n': type(self).calls}, status=self.outcome)


class DecoratorSemanticsTest(TestCase):

    def setUp(self):
        self.user = make_user(role='admin')
        self.factory = APIRequestFactory()
        _Probe.calls = 0

    def _call(self, status_or_exc=201, key='probe-key-1', body=None, user=True):
        _Probe.outcome = status_or_exc
        request = self.factory.post('/probe/', body or {'a': 1}, format='json', HTTP_IDEMPOTENCY_KEY=key)
        if user:
            force_authenticate(request, self.user)
        return _Probe.as_view()(request)

    def test_unstable_outcomes_free_the_key(self):
        for status_code in (400, 401, 403, 404, 409, 422, 429, 500, 503):
            with self.subTest(status=status_code):
                self.assertEqual(self._call(status_code).status_code, status_code)
                self.assertFalse(IdempotencyRecord.objects.exists())

    def test_exception_frees_the_key_and_propagates(self):
        with self.assertRaises(RuntimeError):
            self._call(RuntimeError('boom'))
        self.assertFalse(IdempotencyRecord.objects.exists())
        self.assertEqual(self._call(201).status_code, 201)        # retry runs again
        self.assertEqual(_Probe.calls, 2)

    def test_in_flight_request_gets_409(self):
        now = timezone.now()
        self._call(201)
        IdempotencyRecord.objects.update(status='in_progress', locked_until=now + LEASE)
        resp = self._call(201)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data['code'], 'idempotency_request_in_progress')
        self.assertEqual(_Probe.calls, 1)

    def test_stale_lease_is_taken_over(self):
        self._call(201)
        IdempotencyRecord.objects.update(status='in_progress', locked_until=timezone.now() - timedelta(seconds=1))
        resp = self._call(201)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(_Probe.calls, 2)
        self.assertEqual(IdempotencyRecord.objects.get().status, 'completed')

    def test_expired_record_frees_the_key(self):
        self._call(201)
        IdempotencyRecord.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        resp = self._call(201, body={'different': True})          # would be 422 if still live
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(_Probe.calls, 2)

    def test_anonymous_requests_are_not_tracked(self):
        _Probe.permission_classes = []
        _Probe.authentication_classes = []
        try:
            self._call(201, user=False)
            self._call(201, user=False)
        finally:
            del _Probe.permission_classes, _Probe.authentication_classes
        self.assertEqual(_Probe.calls, 2)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_purge_removes_only_expired(self):
        self._call(201, key='live-key-1')
        self._call(201, key='old-key-1')
        IdempotencyRecord.objects.filter(key='old-key-1').update(expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(purge_idempotency_records(), 1)
        self.assertEqual(list(IdempotencyRecord.objects.values_list('key', flat=True)), ['live-key-1'])
        self.assertEqual(purge_expired(), 0)


class SchemaHeaderTest(TestCase):

    def test_header_documented_on_every_target(self):
        GENERATOR_STATS.reset()
        with GENERATOR_STATS.silence():
            schema = SchemaGenerator().get_schema(request=None, public=True)
        GENERATOR_STATS.reset()
        targets = [
            '/api/v1/leads/', '/api/v1/leads/appointments/',
            '/api/v1/payments/checkout/{deal_id}/', '/api/v1/payments/sepa/{deal_id}/',
            '/api/v1/deals/lock/',
        ]
        for path in targets:
            with self.subTest(path=path):
                names = {p['name'] for p in schema['paths'][path]['post'].get('parameters', [])}
                self.assertIn('Idempotency-Key', names)
