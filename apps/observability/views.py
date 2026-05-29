import logging

import requests as _http_requests
from django.utils.timezone import now
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminUser
from apps.observability.metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)

_ROUTES = [
    "whatsapp.webhook",
    "celery.process_whatsapp",
    "celery.sync_platform",
    "postgres.transaction_lock",
    "meta.outbound_payload",
]


class ObservabilityMetricsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response({
            "error_distribution": MetricsCollector.get_error_distribution(),
            "dlq_depth": MetricsCollector.get_dlq_depth(),
            "p99_latencies": MetricsCollector.get_p99_latencies(),
            "active_connections": MetricsCollector.get_active_connections(),
            "as_of": now().isoformat(),
        })


class TraceSearchView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        route = request.query_params.get("route", "whatsapp.webhook")
        try:
            hours = int(request.query_params.get("hours", 24))
        except (ValueError, TypeError):
            hours = 24
        stats = MetricsCollector.get_percentiles(route, hours)
        return Response({"route": route, **stats})


class OtelHealthView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        from django.conf import settings
        endpoint = getattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        reachable = False
        try:
            resp = _http_requests.head(endpoint, timeout=2)
            reachable = resp.status_code < 500
        except Exception:
            reachable = False

        # Mask credentials from URL
        masked_url = endpoint.split("@")[-1] if "@" in endpoint else endpoint

        queue_depth = -1
        try:
            from apps.observability.observability_hub import ObservabilityHub
            proc = ObservabilityHub._processor
            if proc is not None and hasattr(proc, "_exporter_thread"):
                queue_depth = proc._exporter_thread._queue.qsize()
        except Exception:
            pass

        return Response({
            "exporter_reachable": reachable,
            "exporter_url": masked_url,
            "span_processor_queue_depth": queue_depth,
        })
