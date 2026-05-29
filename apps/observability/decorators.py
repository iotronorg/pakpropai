import functools
import logging
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_trace_context = threading.local()


def trace_whatsapp_webhook(func):
    """
    Wraps a WhatsApp webhook view. Starts span 'whatsapp.webhook.inbound',
    serialises traceparent into thread-local for downstream Celery propagation.
    Fail-open.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            from apps.observability.observability_hub import ObservabilityHub
            from apps.observability.celery_tracing import CeleryTaskTracer

            # args[0] = self (view), args[1] = request
            request = args[1] if len(args) > 1 else kwargs.get("request")
            tracer = ObservabilityHub.get_tracer("whatsapp.webhook")

            attrs = {}
            if request is not None:
                attrs["wa.phone_number_id"] = request.META.get("HTTP_X_WA_PHONE_NUMBER_ID", "")
                attrs["http.method"] = request.method
                org = getattr(request, "org", None)
                if org:
                    attrs["wa.org_id"] = str(org.id)
                # Try to detect message_type from parsed body
                try:
                    import json
                    body = json.loads(request.body.decode())
                    msgs = (
                        body.get("entry", [{}])[0]
                        .get("changes", [{}])[0]
                        .get("value", {})
                        .get("messages", [])
                    )
                    if msgs:
                        attrs["wa.message_type"] = msgs[0].get("type", "")
                except Exception:
                    pass

            with tracer.start_as_current_span("whatsapp.webhook.inbound", attributes=attrs):
                header = CeleryTaskTracer.extract_context_header(None)
                _trace_context.current = header
                return func(*args, **kwargs)
        except Exception:
            logger.warning("trace_whatsapp_webhook failed", exc_info=True)
            _trace_context.current = ""
            return func(*args, **kwargs)

    return wrapper


def trace_celery_task(task_name: str):
    """Decorator for Celery task functions. Reads parent_context from kwargs."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from apps.observability.celery_tracing import CeleryTaskTracer
            from opentelemetry import context as otel_context

            parent_context = kwargs.pop("otel_context", None)
            task_id = ""
            try:
                # Celery bound task: self.request.id
                task_id = args[0].request.id if args else ""
            except Exception:
                pass

            org_id = kwargs.get("org_id")
            span, token = CeleryTaskTracer.instrument_task(task_id, task_name, org_id, parent_context)
            try:
                return func(*args, **kwargs)
            finally:
                try:
                    span.end()
                    if token is not None:
                        otel_context.detach(token)
                except Exception:
                    pass
        return wrapper
    return decorator


@contextmanager
def trace_db_query(span, query: str, table: str = ""):
    """Context manager that records DB query duration as a span event."""
    from apps.observability.celery_tracing import CeleryTaskTracer
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - start) * 1000
        try:
            CeleryTaskTracer.record_db_span(span, query, elapsed, table)
        except Exception:
            pass
