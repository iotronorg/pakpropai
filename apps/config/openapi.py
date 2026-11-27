"""OpenAPI descriptions of the platform configuration endpoint (T-1091, KI-037); keep in step with ``views.py``."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description="Refused; `detail` and `error_info` (ADR-017)")

CONFIG = OpenApiResponse(OpenApiTypes.OBJECT, description=(
    'Every configuration key with its value (secrets read `••••••••` when set, else empty), plus '
    '`setup_complete` (boolean) and `missing_required` (list of keys).'))
config = extend_schema(operation_id='config_retrieve', responses={200: CONFIG, 403: ERROR})
config_update = extend_schema(request=OpenApiTypes.OBJECT, responses={200: CONFIG, 403: ERROR},
                              description='Body: `{key: value}`. Unknown keys and the masked value are skipped.')
