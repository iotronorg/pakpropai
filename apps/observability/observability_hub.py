import logging
from typing import Optional

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.trace import TracerProvider, Span
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import NoOpTracerProvider, Tracer

logger = logging.getLogger(__name__)

_propagator = TraceContextTextMapPropagator()


class ObservabilityHub:
    """Singleton OTel tracer provider. Fail-open — init failure falls back to NoOp."""

    _initialized: bool = False
    _provider: TracerProvider | NoOpTracerProvider = NoOpTracerProvider()
    _processor: Optional[BatchSpanProcessor] = None

    @classmethod
    def setup(cls, service_name: str, otlp_endpoint: str, org_id: str | None = None) -> None:
        if cls._initialized:
            return
        try:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            resource = Resource.create({"service.name": service_name})
            provider = TracerProvider(resource=resource)

            exporter = OTLPSpanExporter(
                endpoint=f"{otlp_endpoint}/v1/traces",
                headers=cls._build_auth_headers(),
            )
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)

            cls._provider = provider
            cls._processor = processor
            trace.set_tracer_provider(provider)
            cls._initialized = True
        except Exception:
            logger.warning("ObservabilityHub.setup failed — falling back to NoOpTracerProvider", exc_info=True)
            cls._provider = NoOpTracerProvider()
            cls._initialized = True

    @classmethod
    def _build_auth_headers(cls) -> dict:
        try:
            from apps.config.services import SystemConfigService
            api_key = SystemConfigService.get("otel_api_key", default="")
            exporter_type = SystemConfigService.get("otel_exporter_type", default="tempo")
            if not api_key:
                return {}
            if exporter_type == "datadog":
                return {"DD-API-KEY": api_key}
            if exporter_type == "newrelic":
                return {"api-key": api_key}
            return {"Authorization": f"Bearer {api_key}"}
        except Exception:
            return {}

    @classmethod
    def get_tracer(cls, name: str) -> Tracer:
        try:
            return cls._provider.get_tracer(name)
        except Exception:
            return NoOpTracerProvider().get_tracer(name)

    @classmethod
    def inject_span_context(cls, carrier: dict) -> None:
        try:
            _propagator.inject(carrier)
        except Exception:
            logger.warning("ObservabilityHub.inject_span_context failed", exc_info=True)

    @classmethod
    def extract_span_context(cls, carrier: dict) -> Context:
        try:
            return _propagator.extract(carrier)
        except Exception:
            logger.warning("ObservabilityHub.extract_span_context failed", exc_info=True)
            from opentelemetry.context import attach, detach
            return Context()

    @classmethod
    def set_tenant_attributes(cls, span, org_id: str) -> None:
        try:
            from django.conf import settings
            span.set_attribute("tenant.org_id", org_id)
            span.set_attribute("tenant.platform", "realtron")
            span.set_attribute("deployment.environment", getattr(settings, "ENVIRONMENT", "development"))
        except Exception:
            logger.warning("ObservabilityHub.set_tenant_attributes failed", exc_info=True)

    @classmethod
    def _reset(cls, exporter: SpanExporter | None = None) -> None:
        """Test helper — force re-initialisation with a custom exporter."""
        cls._initialized = False
        cls._provider = NoOpTracerProvider()
        cls._processor = None
        trace.set_tracer_provider(NoOpTracerProvider())

        if exporter is not None:
            from opentelemetry.sdk.resources import Resource
            resource = Resource.create({"service.name": "test"})
            provider = TracerProvider(resource=resource)
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)
            cls._provider = provider
            cls._processor = processor
            trace.set_tracer_provider(provider)
            cls._initialized = True
