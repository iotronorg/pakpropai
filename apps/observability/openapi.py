"""OpenAPI descriptions of the observability endpoints (T-1091, KI-037); keep in step with ``views.py``."""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ADMIN_ONLY = {403: OpenApiResponse(description='Platform admins only')}

metrics = extend_schema(responses={200: inline_serializer('ObservabilityMetrics', {
    'error_distribution': s.DictField(child=s.IntegerField(), help_text='Error kind → count today'),
    'dlq_depth': s.IntegerField(),
    'p99_latencies': s.DictField(child=s.FloatField(), help_text='Route → p99 in milliseconds'),
    'active_connections': s.DictField(child=s.IntegerField(),
                                      help_text='websocket, celery_workers, redis, postgres'),
    'as_of': s.DateTimeField()}), **ADMIN_ONLY})
otel_health = extend_schema(responses={200: inline_serializer('OtelHealth', {
    'exporter_reachable': s.BooleanField(), 'exporter_url': s.CharField(help_text='Without credentials'),
    'span_processor_queue_depth': s.IntegerField(help_text='-1 when unknown')}), **ADMIN_ONLY})
traces = extend_schema(
    parameters=[OpenApiParameter('route', str, description='Default whatsapp.webhook'),
                OpenApiParameter('hours', int, description='1–720, default 24')],
    responses={200: inline_serializer('RouteLatency', {
        'route': s.CharField(), 'p50_ms': s.FloatField(), 'p95_ms': s.FloatField(), 'p99_ms': s.FloatField(),
        'sample_count': s.IntegerField()}), **ADMIN_ONLY})
