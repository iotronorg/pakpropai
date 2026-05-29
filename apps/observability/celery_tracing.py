import logging
from typing import Optional

logger = logging.getLogger(__name__)

_propagator_cache = None


def _propagator():
    global _propagator_cache
    if _propagator_cache is None:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        _propagator_cache = TraceContextTextMapPropagator()
    return _propagator_cache


class CeleryTaskTracer:
    """Cross-process span propagation for Celery tasks."""

    @staticmethod
    def instrument_task(
        task_id: str,
        task_name: str,
        org_id: Optional[str],
        parent_context: Optional[str],
    ):
        """
        Start a child celery span. Returns (span, context_token) for manual end.
        Fail-open — returns (NoOp span, None) on any error.
        """
        try:
            from opentelemetry import context as otel_context, trace
            from apps.observability.observability_hub import ObservabilityHub

            ctx = otel_context.get_current()
            if parent_context:
                ctx = _propagator().extract({"traceparent": parent_context})

            token = otel_context.attach(ctx)
            tracer = ObservabilityHub.get_tracer("celery.task")
            span = tracer.start_span(
                "celery.task",
                context=ctx,
                attributes={
                    "celery.task_id": task_id or "",
                    "celery.task_name": task_name or "",
                    "messaging.system": "celery",
                },
            )
            if org_id:
                ObservabilityHub.set_tenant_attributes(span, org_id)
            return span, token
        except Exception:
            logger.warning("CeleryTaskTracer.instrument_task failed", exc_info=True)
            return _noop_span(), None

    @staticmethod
    def extract_context_header(span) -> str:
        """Serialise current span to W3C traceparent string for cross-process propagation."""
        try:
            carrier: dict = {}
            _propagator().inject(carrier)
            return carrier.get("traceparent", "")
        except Exception:
            logger.warning("CeleryTaskTracer.extract_context_header failed", exc_info=True)
            return ""

    @staticmethod
    def record_db_span(span, query: str, duration_ms: float, table: str) -> None:
        try:
            span.add_event(
                "db.query",
                attributes={
                    "db.system": "postgresql",
                    "db.statement": query[:200],
                    "db.table": table,
                    "db.query_duration_ms": duration_ms,
                },
            )
        except Exception:
            logger.warning("CeleryTaskTracer.record_db_span failed", exc_info=True)


def _noop_span():
    """Return a non-recording span that safely accepts all calls."""
    from opentelemetry.trace import NonRecordingSpan, INVALID_SPAN_CONTEXT
    return NonRecordingSpan(INVALID_SPAN_CONTEXT)
