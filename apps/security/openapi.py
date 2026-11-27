"""OpenAPI descriptions of the security admin endpoints (T-1091, KI-037); keep in step with ``views.py``."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description="Refused; `detail` and `error_info` (ADR-017)")

chain_verify = extend_schema(
    parameters=[OpenApiParameter('limit', int, description='1–10000, default 1000')],
    responses={200: inline_serializer('SecurityChainVerify', {
        'valid': s.BooleanField(), 'message': s.CharField(), 'records_checked': s.IntegerField()}), 403: ERROR},
    description='Checks the hash chain of the newest security events.')
