"""
OpenAPI descriptions of the WhatsApp endpoints (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts; these describe them for the
schema only. Keep them in step with ``views.py`` and ``provisioning_views.py``.
The Meta webhook is a server-to-server callback, not part of the client API.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

from .serializers import OrgWhatsAppConfigSerializer

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')
LIMIT, OFFSET = OpenApiParameter('limit', int), OpenApiParameter('offset', int)
OUTCOME = inline_serializer('WhatsAppCheckResult', {'ok': s.BooleanField(), 'detail': s.CharField(required=False)})
DETAIL = inline_serializer('WhatsAppProvisioningAccepted', {'detail': s.CharField()})

webhook = extend_schema(exclude=True)

SESSION = {
    'id': s.UUIDField(), 'phone': s.CharField(allow_null=True, help_text='Null when the person withheld it (KI-074)'),
    'user_id': s.UUIDField(allow_null=True), 'state': s.CharField(), 'message_count': s.IntegerField(),
    'started_at': s.DateTimeField(), 'last_message_at': s.DateTimeField(),
}
sessions = extend_schema(
    operation_id='whatsapp_history_list',
    parameters=[OpenApiParameter('phone', str, description='Prefix'), OpenApiParameter('state', str),
                LIMIT, OFFSET],
    responses={200: inline_serializer('WhatsAppSessionPage', {
        'count': s.IntegerField(), 'limit': s.IntegerField(), 'offset': s.IntegerField(),
        'results': inline_serializer('WhatsAppSessionSummary', SESSION, many=True)}), 403: ERROR},
    description='Admins: all; agents: their leads; organization owners: their organization\'s leads. '
                'Limit 50 by default, at most 200.')
session = extend_schema(
    parameters=[OpenApiParameter('direction', str, enum=['inbound', 'outbound']), LIMIT, OFFSET],
    responses={200: inline_serializer('WhatsAppSessionThread', {
        'session': inline_serializer('WhatsAppSessionHeader', SESSION),
        'messages': inline_serializer('WhatsAppMessagePage', {
            'count': s.IntegerField(), 'limit': s.IntegerField(), 'offset': s.IntegerField(),
            'results': inline_serializer('WhatsAppThreadMessage', {
                'id': s.CharField(), 'wa_message_id': s.CharField(allow_null=True, allow_blank=True),
                'direction': s.CharField(), 'msg_type': s.CharField(), 'body': s.CharField(allow_blank=True),
                'media_url': s.CharField(allow_null=True, allow_blank=True), 'created_at': s.DateTimeField()},
                many=True)})}), 403: ERROR, 404: ERROR},
    description='Limit 100 by default, at most 500; oldest first.')
config = extend_schema(responses={200: OrgWhatsAppConfigSerializer, 403: ERROR, 404: ERROR},
                       description='Secrets come back masked.')
config_update = extend_schema(request=OrgWhatsAppConfigSerializer,
                              responses={200: OrgWhatsAppConfigSerializer, 400: ERROR, 403: ERROR, 404: ERROR},
                              description='Sending the masked value leaves a secret unchanged.')
verify = extend_schema(request=None, responses={200: OUTCOME, 400: OUTCOME, 403: ERROR, 404: ERROR, 502: OUTCOME},
                       description='Checks the stored credentials against Meta.')
test_message = extend_schema(request=None, responses={200: OUTCOME, 400: OUTCOME, 403: ERROR, 404: ERROR,
                                                      502: OUTCOME},
                             description='Sends a test message to the caller\'s own phone.')
sync = extend_schema(request=None, responses={200: inline_serializer('WhatsAppProfileSync', {
    'status': s.CharField(), 'synced_at': s.DateTimeField(allow_null=True, help_text='The previous sync')}),
    400: ERROR, 403: ERROR, 404: ERROR})
take_control = extend_schema(request=None, responses={
    200: inline_serializer('WhatsAppTakeControl', {'conversation_mode': s.CharField(),
                                                   'lock_ttl': s.IntegerField(help_text='Seconds')}),
    404: ERROR, 409: inline_serializer('WhatsAppSessionHeld', {'detail': s.CharField(), 'held_by': s.CharField()})},
    description='An agent takes the conversation over from the assistant.')
release_control = extend_schema(request=None, responses={
    200: inline_serializer('WhatsAppReleaseControl', {'conversation_mode': s.CharField()}), 403: ERROR, 404: ERROR},
    description='Hands the conversation back to the assistant.')


def _record():
    from .provisioning_views import ProvisioningRecordSerializer
    return ProvisioningRecordSerializer


def provisioning_status(view_method):
    return extend_schema(responses={200: _record(), 404: ERROR})(view_method)


provisioning_start = extend_schema(request=None, responses={202: DETAIL, 400: ERROR, 404: ERROR, 409: ERROR},
                                   description='Moves the organization to its own WhatsApp number.')
provisioning_retry = extend_schema(request=None, responses={202: DETAIL, 400: ERROR, 404: ERROR},
                                   description='Only after a failed run.')
