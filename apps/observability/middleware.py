import logging
import time

logger = logging.getLogger(__name__)

_TRACKED_ROUTES = {
    "whatsapp.webhook",
    "celery.process_whatsapp",
    "celery.sync_platform",
    "postgres.transaction_lock",
    "meta.outbound_payload",
}


class OtelDjangoMiddleware:
    """
    Lightweight OTel root-span middleware. Fail-open — any exception logs WARNING
    and passes through. No DB calls, no blocking I/O.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._process_request(request)
        response = self.get_response(request)
        self._process_response(request, response)
        return response

    def _process_request(self, request) -> None:
        try:
            from apps.observability.observability_hub import ObservabilityHub
            from opentelemetry import context as otel_context

            parent_ctx = ObservabilityHub.extract_span_context(
                {"traceparent": request.META.get("HTTP_TRACEPARENT", "")}
            )
            token = otel_context.attach(parent_ctx)

            tracer = ObservabilityHub.get_tracer("django.http")
            span = tracer.start_span("http.server.request")

            span.set_attribute("http.method", request.method)
            span.set_attribute("http.url", request.build_absolute_uri())
            span.set_attribute("http.user_agent", request.META.get("HTTP_USER_AGENT", ""))

            view_name = self._resolve_view(request)
            if view_name:
                span.set_attribute("http.route", view_name)

            org = getattr(request, "org", None)
            if org:
                ObservabilityHub.set_tenant_attributes(span, str(org.id))

            request._otel_span = span
            request._otel_ctx_token = token
        except Exception:
            logger.warning("OtelDjangoMiddleware.process_request failed", exc_info=True)
            request._otel_span = None

    def _process_response(self, request, response) -> None:
        try:
            span = getattr(request, "_otel_span", None)
            if span is None:
                return
            span.set_attribute("http.status_code", response.status_code)

            # Record 5xx errors for MetricsCollector
            if response.status_code >= 500:
                try:
                    from apps.observability.metrics_collector import MetricsCollector
                    MetricsCollector.record_error(response.status_code, "5xx")
                except Exception:
                    pass

            span.end()

            token = getattr(request, "_otel_ctx_token", None)
            if token is not None:
                from opentelemetry import context as otel_context
                otel_context.detach(token)
        except Exception:
            logger.warning("OtelDjangoMiddleware.process_response failed", exc_info=True)
        finally:
            request._otel_span = None

    @staticmethod
    def _resolve_view(request) -> str:
        try:
            from django.urls import resolve
            match = resolve(request.path_info)
            return f"{match.namespace}:{match.url_name}" if match.namespace else (match.url_name or "")
        except Exception:
            return ""
