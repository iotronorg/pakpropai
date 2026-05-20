"""
Observability middleware test suite.

Covers:
  - apps.core.context  — context var get/set/clear semantics
  - apps.core.log_filters.TraceContextFilter — field injection into LogRecords
  - apps.core.middleware.TraceContextMiddleware — lifecycle + org_id resolution
  - End-to-end: log lines emitted during a request carry organization_id/lead_id
"""
import logging
import uuid
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings

from apps.core.context import (
    clear_trace_context,
    get_lead_id,
    get_organization_id,
    set_trace_context,
)
from apps.core.log_filters import TraceContextFilter
from apps.core.middleware import TraceContextMiddleware


# ── Context var tests ─────────────────────────────────────────────────────────

class TraceContextVarTests(TestCase):

    def setUp(self):
        clear_trace_context()

    def tearDown(self):
        clear_trace_context()

    def test_defaults_are_empty_strings(self):
        self.assertEqual(get_organization_id(), '')
        self.assertEqual(get_lead_id(), '')

    def test_set_organization_id(self):
        org_id = str(uuid.uuid4())
        set_trace_context(organization_id=org_id)
        self.assertEqual(get_organization_id(), org_id)

    def test_set_lead_id(self):
        lead_id = str(uuid.uuid4())
        set_trace_context(lead_id=lead_id)
        self.assertEqual(get_lead_id(), lead_id)

    def test_set_both(self):
        org_id = str(uuid.uuid4())
        lead_id = str(uuid.uuid4())
        set_trace_context(organization_id=org_id, lead_id=lead_id)
        self.assertEqual(get_organization_id(), org_id)
        self.assertEqual(get_lead_id(), lead_id)

    def test_clear_resets_both_fields(self):
        set_trace_context(organization_id='org-1', lead_id='lead-1')
        clear_trace_context()
        self.assertEqual(get_organization_id(), '')
        self.assertEqual(get_lead_id(), '')

    def test_set_with_none_org_id_stores_empty_string(self):
        # Prevents str(None) = 'None' ending up in log lines
        set_trace_context(organization_id=None)
        self.assertEqual(get_organization_id(), '')

    def test_set_with_integer_coerces_to_string(self):
        set_trace_context(organization_id=42, lead_id=99)
        self.assertEqual(get_organization_id(), '42')
        self.assertEqual(get_lead_id(), '99')

    def test_partial_set_does_not_overwrite_other_field(self):
        org_id = str(uuid.uuid4())
        lead_id = str(uuid.uuid4())
        set_trace_context(organization_id=org_id)
        set_trace_context(lead_id=lead_id)
        # Both fields should still be set
        self.assertEqual(get_organization_id(), org_id)
        self.assertEqual(get_lead_id(), lead_id)


# ── TraceContextFilter tests ──────────────────────────────────────────────────

class TraceContextFilterTests(TestCase):

    def setUp(self):
        clear_trace_context()
        self.filter = TraceContextFilter()

    def tearDown(self):
        clear_trace_context()

    def _make_record(self) -> logging.LogRecord:
        return logging.LogRecord(
            name='apps.test', level=logging.INFO,
            pathname='', lineno=0, msg='test message',
            args=(), exc_info=None,
        )

    def test_filter_returns_true(self):
        record = self._make_record()
        self.assertTrue(self.filter.filter(record))

    def test_injects_dash_when_context_empty(self):
        record = self._make_record()
        self.filter.filter(record)
        self.assertEqual(record.organization_id, '-')
        self.assertEqual(record.lead_id, '-')

    def test_injects_organization_id_when_set(self):
        org_id = str(uuid.uuid4())
        set_trace_context(organization_id=org_id)
        record = self._make_record()
        self.filter.filter(record)
        self.assertEqual(record.organization_id, org_id)

    def test_injects_lead_id_when_set(self):
        lead_id = str(uuid.uuid4())
        set_trace_context(lead_id=lead_id)
        record = self._make_record()
        self.filter.filter(record)
        self.assertEqual(record.lead_id, lead_id)

    def test_injects_both_when_both_set(self):
        org_id = str(uuid.uuid4())
        lead_id = str(uuid.uuid4())
        set_trace_context(organization_id=org_id, lead_id=lead_id)
        record = self._make_record()
        self.filter.filter(record)
        self.assertEqual(record.organization_id, org_id)
        self.assertEqual(record.lead_id, lead_id)

    def test_reverts_to_dash_after_context_cleared(self):
        set_trace_context(organization_id='org-1')
        clear_trace_context()
        record = self._make_record()
        self.filter.filter(record)
        self.assertEqual(record.organization_id, '-')


# ── TraceContextMiddleware tests ──────────────────────────────────────────────

class TraceContextMiddlewareTests(TestCase):

    def setUp(self):
        clear_trace_context()
        self.factory = RequestFactory()

    def tearDown(self):
        clear_trace_context()

    def _middleware(self, get_response=None):
        if get_response is None:
            get_response = lambda req: MagicMock(status_code=200)
        return TraceContextMiddleware(get_response)

    def test_sets_org_id_from_request_organization(self):
        org = MagicMock()
        org.pk = uuid.uuid4()

        captured = {}

        def get_response(request):
            captured['org_id'] = get_organization_id()
            return MagicMock(status_code=200)

        request = self.factory.get('/')
        request.organization = org
        self._middleware(get_response)(request)

        self.assertEqual(captured['org_id'], str(org.pk))

    def test_org_id_empty_when_no_organization_on_request(self):
        captured = {}

        def get_response(request):
            captured['org_id'] = get_organization_id()
            return MagicMock(status_code=200)

        request = self.factory.get('/')
        request.organization = None
        self._middleware(get_response)(request)

        self.assertEqual(captured['org_id'], '')

    def test_org_id_empty_when_organization_attr_missing(self):
        captured = {}

        def get_response(request):
            captured['org_id'] = get_organization_id()
            return MagicMock(status_code=200)

        request = self.factory.get('/')
        # No request.organization attribute at all
        self._middleware(get_response)(request)

        self.assertEqual(captured['org_id'], '')

    def test_context_cleared_after_response(self):
        org = MagicMock()
        org.pk = uuid.uuid4()

        request = self.factory.get('/')
        request.organization = org
        self._middleware()(request)

        # After the request completes, context must be reset
        self.assertEqual(get_organization_id(), '')
        self.assertEqual(get_lead_id(), '')

    def test_context_cleared_even_when_view_raises(self):
        org = MagicMock()
        org.pk = uuid.uuid4()

        def crashing_view(request):
            raise RuntimeError('simulated view crash')

        request = self.factory.get('/')
        request.organization = org
        mw = self._middleware(crashing_view)

        with self.assertRaises(RuntimeError):
            mw(request)

        self.assertEqual(get_organization_id(), '')

    def test_lead_id_set_mid_request_visible_to_later_log(self):
        """
        Simulate a view that identifies a lead and calls set_trace_context.
        The filter must see the updated lead_id for log records after that call.
        """
        lead_id = str(uuid.uuid4())
        filter_ = TraceContextFilter()
        captured = {}

        def get_response(request):
            set_trace_context(lead_id=lead_id)
            record = logging.LogRecord(
                'test', logging.INFO, '', 0, 'msg', (), None
            )
            filter_.filter(record)
            captured['lead_id'] = record.lead_id
            return MagicMock(status_code=200)

        request = self.factory.get('/')
        request.organization = None
        self._middleware(get_response)(request)

        self.assertEqual(captured['lead_id'], lead_id)


# ── End-to-end log output tests ───────────────────────────────────────────────

class LogOutputIntegrationTests(TestCase):
    """
    Verifies that log lines emitted inside a request carry organization_id and
    lead_id by using assertLogs() to capture the live log stream.
    """

    def setUp(self):
        clear_trace_context()
        self.factory = RequestFactory()

    def tearDown(self):
        clear_trace_context()

    def test_log_emitted_during_request_carries_org_id(self):
        """
        The filter must be applied while the request context is still live.
        assertLogs captures raw records; we simulate a filter-attached handler
        by applying the filter inside get_response (before context is cleared).
        """
        org = MagicMock()
        org.pk = uuid.uuid4()
        test_logger = logging.getLogger('apps.core.test_obs')
        filter_ = TraceContextFilter()
        captured: dict = {}

        def get_response(request):
            # Build a record and run the filter while context vars are set
            record = logging.LogRecord(
                'apps.core.test_obs', logging.INFO, '', 0, 'processing request', (), None
            )
            filter_.filter(record)
            captured['organization_id'] = record.organization_id
            captured['lead_id'] = record.lead_id
            test_logger.info('processing request')
            return MagicMock(status_code=200)

        request = self.factory.get('/')
        request.organization = org

        with self.assertLogs('apps.core.test_obs', level='INFO'):
            TraceContextMiddleware(get_response)(request)

        self.assertEqual(captured['organization_id'], str(org.pk))
        self.assertEqual(captured['lead_id'], '-')

    def test_log_emitted_outside_request_has_dash_fields(self):
        test_logger = logging.getLogger('apps.core.test_obs_outside')
        record = logging.LogRecord(
            'apps.core.test_obs_outside', logging.INFO, '', 0, 'outside', (), None
        )
        TraceContextFilter().filter(record)
        self.assertEqual(record.organization_id, '-')
        self.assertEqual(record.lead_id, '-')
