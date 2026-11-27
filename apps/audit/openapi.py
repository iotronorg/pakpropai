"""
OpenAPI descriptions of the property-audit admin endpoints (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts; these describe them for the
schema only. Keep them in step with ``views.py``.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')

AUDIT = {
    'id': s.IntegerField(), 'phone': s.CharField(), 'city': s.CharField(), 'location': s.CharField(),
    'property_type': s.CharField(), 'area_size': s.FloatField(allow_null=True), 'area_unit': s.CharField(),
    'estimated_value': s.IntegerField(allow_null=True), 'currency': s.CharField(),
    'risk_score': s.IntegerField(allow_null=True), 'investment_grade': s.CharField(allow_blank=True),
    'liquidity_score': s.IntegerField(allow_null=True), 'has_pdf': s.BooleanField(), 'created_at': s.DateTimeField(),
}
EDITABLE = {
    'price_per_unit_min': s.IntegerField(), 'price_per_unit_max': s.IntegerField(), 'size_unit': s.CharField(),
    'currency': s.CharField(), 'yield_pct': s.FloatField(), 'appr_pct': s.FloatField(), 'liq_months': s.IntegerField(),
    'approved': s.BooleanField(allow_null=True), 'is_active': s.BooleanField(),
}
BENCHMARK = inline_serializer('AuditBenchmark', {
    'id': s.IntegerField(), 'city': s.CharField(), 'location_key': s.CharField(), 'country': s.CharField(),
    **EDITABLE, 'updated_at': s.DateTimeField()})


def _optional(fields):
    return {name: type(f)(required=False, **({'allow_null': True} if f.allow_null else {}))
            for name, f in fields.items()}


audits = extend_schema(operation_id='audit_list', responses=inline_serializer('PropertyAuditRow', AUDIT, many=True),
                       description='The 100 newest property audits.')
benchmarks = extend_schema(parameters=[OpenApiParameter('city', str)],
                           responses=type(BENCHMARK)(many=True))
benchmark_create = extend_schema(
    request=inline_serializer('AuditBenchmarkCreate', {
        'city': s.CharField(), 'location_key': s.CharField(), 'country': s.CharField(required=False),
        **{n: f for n, f in EDITABLE.items() if n in ('price_per_unit_min', 'price_per_unit_max', 'yield_pct',
                                                       'appr_pct', 'liq_months')},
        **_optional({n: f for n, f in EDITABLE.items() if n in ('size_unit', 'currency', 'approved', 'is_active')})}),
    responses={201: BENCHMARK, 400: ERROR, 409: ERROR})
benchmark_update = extend_schema(request=inline_serializer('AuditBenchmarkUpdate', _optional(EDITABLE)),
                                 responses={200: BENCHMARK, 404: ERROR})
benchmark_delete = extend_schema(responses={204: None, 404: ERROR})
