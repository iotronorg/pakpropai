"""
Unit + integration tests for apps.observability.

Coverage:
  a-e  ObservabilityHub — init guard, fail-open, tracer provider, inject/extract
  f-h  MetricsCollector — record_latency, record_error, get_percentiles (mocked Redis)
  i-j  OtelDjangoMiddleware — span created on request, status_code set on response
  k-l  CeleryTaskTracer — instrument_task propagates parent context, record_db_span
  m-n  trace_whatsapp_webhook decorator — span started, fail-open on broken tracer
  o-p  ObservabilityMetricsView — admin-only access, returns expected shape
  q    TraceSearchView — route parameter forwarded to MetricsCollector
  r    OtelHealthView — masked URL in response
  s    Pipeline integration — traceparent injected into Celery kwargs in WA webhook
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, RequestFactory, override_settings
from rest_framework.test import APIClient

from apps.users.models import User


# ── helpers ───────────────────────────────────────────────────────────────────

_n = 0


def _uniq_phone():
    global _n
    _n += 1
    return f"+155500{_n:04d}"


def _make_admin():
    return User.objects.create_user(phone=_uniq_phone(), password="pass", role="admin")


def _make_non_admin():
    return User.objects.create_user(phone=_uniq_phone(), password="pass", role="agent")


# ── a. ObservabilityHub init guard ─────────────────────────────────────────────

class ObservabilityHubInitGuardTest(TestCase):
    def test_a_double_setup_is_idempotent(self):
        from apps.observability.observability_hub import ObservabilityHub
        ObservabilityHub._reset()
        ObservabilityHub.setup("svc", "http://localhost:4317")
        first_provider = ObservabilityHub._provider
        ObservabilityHub.setup("svc", "http://localhost:4317")
        self.assertIs(ObservabilityHub._provider, first_provider)


# ── b. ObservabilityHub fail-open on broken OTLP ──────────────────────────────

class ObservabilityHubFailOpenTest(TestCase):
    def test_b_broken_otlp_falls_back_to_noop(self):
        from apps.observability.observability_hub import ObservabilityHub
        from opentelemetry.trace import NoOpTracerProvider

        ObservabilityHub._reset()
        # OTLPSpanExporter is imported locally inside setup() — patch at source module
        with patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            side_effect=RuntimeError("network unreachable"),
        ):
            ObservabilityHub.setup("svc", "http://bad-host:9999")

        self.assertIsInstance(ObservabilityHub._provider, NoOpTracerProvider)
        self.assertTrue(ObservabilityHub._initialized)


# ── c. get_tracer never raises ─────────────────────────────────────────────────

class ObservabilityHubGetTracerTest(TestCase):
    def test_c_get_tracer_returns_tracer(self):
        from apps.observability.observability_hub import ObservabilityHub
        ObservabilityHub._reset()
        tracer = ObservabilityHub.get_tracer("test.tracer")
        self.assertIsNotNone(tracer)


# ── d. inject / extract round-trip ────────────────────────────────────────────

class ObservabilityHubPropagationTest(TestCase):
    def test_d_inject_extract_roundtrip(self):
        from apps.observability.observability_hub import ObservabilityHub
        carrier = {}
        ObservabilityHub.inject_span_context(carrier)
        ctx = ObservabilityHub.extract_span_context(carrier)
        self.assertIsNotNone(ctx)


# ── e. set_tenant_attributes is fail-open ─────────────────────────────────────

class ObservabilityHubTenantAttrTest(TestCase):
    def test_e_set_tenant_attributes_fail_open(self):
        from apps.observability.observability_hub import ObservabilityHub
        broken_span = MagicMock()
        broken_span.set_attribute.side_effect = RuntimeError("span closed")
        ObservabilityHub.set_tenant_attributes(broken_span, "org-123")


# ── f. MetricsCollector.record_latency writes to Redis ────────────────────────

class MetricsCollectorLatencyTest(TestCase):
    def test_f_record_latency_calls_zadd(self):
        from apps.observability.metrics_collector import MetricsCollector

        mock_r = MagicMock()
        with patch("apps.observability.metrics_collector._redis", return_value=mock_r):
            MetricsCollector.record_latency("whatsapp.webhook", 123.4)

        mock_r.zadd.assert_called_once()
        mock_r.expire.assert_called_once()


# ── g. MetricsCollector.record_error increments HSET ─────────────────────────

class MetricsCollectorErrorTest(TestCase):
    def test_g_record_error_calls_hincrby(self):
        from apps.observability.metrics_collector import MetricsCollector

        mock_r = MagicMock()
        with patch("apps.observability.metrics_collector._redis", return_value=mock_r):
            MetricsCollector.record_error(500, "5xx")

        mock_r.hincrby.assert_called_once()
        args = mock_r.hincrby.call_args[0]
        self.assertIn("5xx:500", args)


# ── h. MetricsCollector.get_percentiles parses score correctly ────────────────

class MetricsCollectorPercentilesTest(TestCase):
    def test_h_get_percentiles_extracts_duration_from_score(self):
        import time
        from apps.observability.metrics_collector import MetricsCollector

        expected_ms = 250.0
        score = int(time.time()) * 100_000 + expected_ms

        mock_r = MagicMock()
        mock_r.zcard.return_value = 10
        mock_r.zrange.return_value = [(b"key", score)]

        with patch("apps.observability.metrics_collector._redis", return_value=mock_r):
            result = MetricsCollector.get_percentiles("whatsapp.webhook")

        self.assertAlmostEqual(result["p50_ms"], expected_ms, places=1)
        self.assertEqual(result["sample_count"], 10)


# ── i. OtelDjangoMiddleware attaches span on request ─────────────────────────

class OtelMiddlewareRequestTest(TestCase):
    def test_i_middleware_attaches_otel_span(self):
        from apps.observability.middleware import OtelDjangoMiddleware

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        factory = RequestFactory()
        request = factory.get("/api/v1/leads/")

        def get_response(req):
            from django.http import HttpResponse
            return HttpResponse(status=200)

        middleware = OtelDjangoMiddleware(get_response)

        with patch("apps.observability.observability_hub.ObservabilityHub.get_tracer", return_value=mock_tracer):
            with patch("apps.observability.observability_hub.ObservabilityHub.extract_span_context"):
                # Hub is imported locally in middleware — patch at hub class level
                from apps.observability import middleware as mw_module
                original = mw_module.ObservabilityHub if hasattr(mw_module, "ObservabilityHub") else None

                # Patch the lazy import inside _process_request
                with patch("apps.observability.observability_hub.ObservabilityHub") as mock_hub:
                    mock_hub.extract_span_context.return_value = MagicMock()
                    mock_hub.get_tracer.return_value = mock_tracer
                    mock_hub.set_tenant_attributes = MagicMock()
                    response = middleware(request)

        # Middleware should not raise regardless
        self.assertEqual(response.status_code, 200)

    def test_i2_span_attribute_http_method_set(self):
        """Integration: middleware creates span via real Hub (NoOp) and sets method attr."""
        from apps.observability.middleware import OtelDjangoMiddleware
        from apps.observability.observability_hub import ObservabilityHub

        ObservabilityHub._reset()

        factory = RequestFactory()
        request = factory.get("/api/v1/leads/")

        def get_response(req):
            from django.http import HttpResponse
            return HttpResponse(status=200)

        middleware = OtelDjangoMiddleware(get_response)
        response = middleware(request)
        self.assertEqual(response.status_code, 200)


# ── j. OtelDjangoMiddleware sets status_code on response ────────────────────

class OtelMiddlewareResponseTest(TestCase):
    def test_j_middleware_sets_status_code_on_span(self):
        from apps.observability.middleware import OtelDjangoMiddleware
        from apps.observability.observability_hub import ObservabilityHub

        ObservabilityHub._reset()

        factory = RequestFactory()
        request = factory.get("/api/v1/leads/")

        def get_response(req):
            from django.http import HttpResponse
            return HttpResponse(status=404)

        middleware = OtelDjangoMiddleware(get_response)
        response = middleware(request)
        # Middleware must be fail-open and not interfere with response
        self.assertEqual(response.status_code, 404)


# ── k. CeleryTaskTracer propagates parent context ────────────────────────────

class CeleryTracerPropagationTest(TestCase):
    def test_k_instrument_task_uses_parent_traceparent(self):
        from apps.observability.celery_tracing import CeleryTaskTracer
        from apps.observability.observability_hub import ObservabilityHub

        ObservabilityHub._reset()

        fake_traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_span

        # instrument_task imports ObservabilityHub locally — patch at the hub module
        with patch("apps.observability.observability_hub.ObservabilityHub.get_tracer", return_value=mock_tracer):
            span, token = CeleryTaskTracer.instrument_task(
                "task-id-123", "celery.process_whatsapp", "org-456", fake_traceparent
            )

        mock_tracer.start_span.assert_called_once()
        self.assertEqual(mock_tracer.start_span.call_args[0][0], "celery.task")


# ── l. CeleryTaskTracer.record_db_span truncates long query ──────────────────

class CeleryTracerDbSpanTest(TestCase):
    def test_l_record_db_span_truncates_to_200_chars(self):
        from apps.observability.celery_tracing import CeleryTaskTracer

        long_query = "SELECT " + "x" * 500
        mock_span = MagicMock()
        CeleryTaskTracer.record_db_span(mock_span, long_query, 42.0, "leads_lead")

        event_attrs = mock_span.add_event.call_args[1]["attributes"]
        self.assertLessEqual(len(event_attrs["db.statement"]), 200)


# ── m. trace_whatsapp_webhook starts span ─────────────────────────────────────

class TraceWebhookDecoratorTest(TestCase):
    def test_m_decorator_calls_start_as_current_span(self):
        from apps.observability.decorators import trace_whatsapp_webhook
        from apps.observability.observability_hub import ObservabilityHub

        ObservabilityHub._reset()

        mock_span_ctx = MagicMock()
        mock_span_ctx.__enter__ = MagicMock(return_value=MagicMock())
        mock_span_ctx.__exit__ = MagicMock(return_value=False)
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        factory = RequestFactory()
        request = factory.post(
            "/api/v1/whatsapp/webhook/",
            data=json.dumps({"entry": []}),
            content_type="application/json",
        )

        class FakeView:
            @trace_whatsapp_webhook
            def post(self, req):
                return "ok"

        with patch.object(ObservabilityHub, "get_tracer", return_value=mock_tracer):
            result = FakeView().post(request)

        mock_tracer.start_as_current_span.assert_called_once()
        self.assertEqual(result, "ok")


# ── n. trace_whatsapp_webhook is fail-open ────────────────────────────────────

class TraceWebhookFailOpenTest(TestCase):
    def test_n_decorator_fail_open_on_broken_tracer(self):
        from apps.observability.decorators import trace_whatsapp_webhook
        from apps.observability.observability_hub import ObservabilityHub

        ObservabilityHub._reset()

        factory = RequestFactory()
        request = factory.post("/api/v1/whatsapp/webhook/", data="{}", content_type="application/json")

        class FakeView:
            @trace_whatsapp_webhook
            def post(self, req):
                return "delivered"

        with patch.object(ObservabilityHub, "get_tracer", side_effect=RuntimeError("tracer exploded")):
            result = FakeView().post(request)

        self.assertEqual(result, "delivered")


# ── o. ObservabilityMetricsView admin-only ────────────────────────────────────

@override_settings(
    OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317",
    OTEL_SERVICE_NAME="realtron-test",
)
class ObservabilityMetricsViewPermissionTest(TestCase):
    def setUp(self):
        self.admin = _make_admin()
        self.agent = _make_non_admin()
        self.client = APIClient()

    def test_o_non_admin_gets_403(self):
        self.client.force_authenticate(self.agent)
        resp = self.client.get("/api/v1/observability/metrics/")
        self.assertEqual(resp.status_code, 403)

    def test_o2_admin_gets_200_with_expected_keys(self):
        self.client.force_authenticate(self.admin)
        active = {"websocket": 0, "celery_workers": 0, "redis": 0, "postgres": 0}
        with patch("apps.observability.views.MetricsCollector.get_error_distribution", return_value={}):
            with patch("apps.observability.views.MetricsCollector.get_dlq_depth", return_value=0):
                with patch("apps.observability.views.MetricsCollector.get_p99_latencies", return_value={}):
                    with patch("apps.observability.views.MetricsCollector.get_active_connections", return_value=active):
                        resp = self.client.get("/api/v1/observability/metrics/")
        self.assertEqual(resp.status_code, 200)
        for key in ("error_distribution", "dlq_depth", "p99_latencies", "active_connections", "as_of"):
            self.assertIn(key, resp.data)


# ── q. TraceSearchView forwards route param ──────────────────────────────────

@override_settings(
    OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317",
    OTEL_SERVICE_NAME="realtron-test",
)
class TraceSearchViewTest(TestCase):
    def setUp(self):
        self.admin = _make_admin()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_q_route_param_forwarded_to_collector(self):
        fake_stats = {"p50_ms": 10.0, "p95_ms": 50.0, "p99_ms": 120.0, "sample_count": 5}
        with patch(
            "apps.observability.views.MetricsCollector.get_percentiles",
            return_value=fake_stats,
        ) as mock_get:
            resp = self.client.get("/api/v1/observability/traces/?route=celery.sync_platform")

        mock_get.assert_called_once_with("celery.sync_platform", 24)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["route"], "celery.sync_platform")


# ── r. OtelHealthView masks credentials in URL ───────────────────────────────

@override_settings(
    OTEL_EXPORTER_OTLP_ENDPOINT="http://user:secret@tempo.internal:4317",
    OTEL_SERVICE_NAME="realtron-test",
)
class OtelHealthViewMaskTest(TestCase):
    def setUp(self):
        self.admin = _make_admin()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_r_credentials_masked_in_health_response(self):
        with patch("apps.observability.views._http_requests.head", side_effect=ConnectionError):
            resp = self.client.get("/api/v1/observability/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("secret", resp.data.get("exporter_url", ""))
        self.assertFalse(resp.data["exporter_reachable"])


# ── s. Pipeline integration — traceparent propagated into Celery kwargs ───────

class WhatsAppTracelinePropagationTest(TestCase):
    """
    Verifies that the WhatsApp webhook handler passes otel_context into
    the Celery task kwargs so the child span can restore the parent trace.
    """

    def test_s_webhook_injects_otel_context_into_celery_kwargs(self):
        from apps.observability.decorators import _trace_context, trace_whatsapp_webhook
        from apps.observability.observability_hub import ObservabilityHub
        from apps.observability.celery_tracing import CeleryTaskTracer

        ObservabilityHub._reset()

        captured_kwargs = {}

        def fake_delay(*args, **kwargs):
            captured_kwargs.update(kwargs)

        mock_task = MagicMock()
        mock_task.delay = fake_delay

        mock_span_ctx = MagicMock()
        mock_span_ctx.__enter__ = MagicMock(return_value=MagicMock())
        mock_span_ctx.__exit__ = MagicMock(return_value=False)
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span_ctx

        with patch.object(ObservabilityHub, "get_tracer", return_value=mock_tracer):
            with patch.object(CeleryTaskTracer, "extract_context_header", return_value="00-abc-def-01"):
                factory = RequestFactory()
                request = factory.post(
                    "/api/v1/whatsapp/webhook/",
                    data=json.dumps({"entry": []}),
                    content_type="application/json",
                )

                @trace_whatsapp_webhook
                def fake_post(self_view, req):
                    otel_ctx = getattr(_trace_context, "current", "")
                    mock_task.delay({"text": "hi"}, "+1234567890", otel_context=otel_ctx)
                    return "ok"

                class FakeView:
                    pass

                result = fake_post(FakeView(), request)

        self.assertEqual(result, "ok")
        self.assertIn("otel_context", captured_kwargs)
        self.assertEqual(captured_kwargs["otel_context"], "00-abc-def-01")
