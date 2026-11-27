"""
OpenAPI descriptions of the voice endpoints (T-1091, KI-037).

Plain ``APIView``s; these describe them for the schema only. Keep them in step
with ``views.py``. The Twilio webhook is a signed server-to-server callback.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

from .serializers import OrgVoiceConfigSerializer, VoiceCallSessionSerializer

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')

webhook = extend_schema(exclude=True)

config = extend_schema(responses={200: OrgVoiceConfigSerializer, 400: ERROR})
config_update = extend_schema(request=OrgVoiceConfigSerializer,
                              responses={200: OrgVoiceConfigSerializer, 400: ERROR})
calls = extend_schema(operation_id='voice_calls_list', parameters=[OpenApiParameter('status', str)],
                      responses=VoiceCallSessionSerializer(many=True), description='The 200 newest.')
call = extend_schema(responses={200: VoiceCallSessionSerializer, 404: ERROR})
barge_in = extend_schema(request=None, responses={
    200: inline_serializer('VoiceBargeIn', {'latency_ms': s.FloatField()}), 404: ERROR, 409: ERROR},
    description='An agent takes the call over from the assistant.')
initiate = extend_schema(
    request=inline_serializer('VoiceCallInitiateRequest', {'lead_id': s.UUIDField()}),
    responses={202: inline_serializer('VoiceCallInitiated', {'call_sid': s.CharField()}),
               400: ERROR, 403: ERROR, 404: ERROR,
               409: OpenApiResponse(description='The person stopped sharing contact details'), 502: ERROR},
    description='Frozen for the MVP unless `feature_voice_calls` is on (MVP_SCOPE §4.2).')
