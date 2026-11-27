"""OpenAPI descriptions of the SLA and circuit-breaker endpoints (T-1091, KI-037); keep in step with ``views.py``."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description="Refused; `detail` and `error_info` (ADR-017)")

sla_status = extend_schema(responses={200: inline_serializer('SlaStatus', {
    'circuits': s.DictField(child=s.CharField(), help_text='service → CLOSED | OPEN | HALF_OPEN'),
    'outages': s.DictField(child=inline_serializer('SlaOutage', {
        'is_open': s.BooleanField(), 'error_rate': s.FloatField(), 'updated_at': s.CharField(allow_blank=True)})),
    'queue_isolation': inline_serializer('SlaQueueIsolation', {
        'isolated_orgs': s.ListField(child=s.CharField()), 'total_monitored': s.IntegerField()}),
    'as_of': s.DateTimeField()}), 403: ERROR})
circuit_reset = extend_schema(request=None, responses={200: inline_serializer('SlaCircuitReset', {
    'service': s.CharField(), 'state': s.CharField()}), 403: ERROR, 404: ERROR},
    description='Forces a circuit back to CLOSED.')
