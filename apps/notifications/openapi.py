"""
OpenAPI descriptions of the notification inbox (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts; these describe them for the
schema only. Keep them in step with ``views.py``.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as s

inbox = extend_schema(
    operation_id='notifications_list',
    parameters=[OpenApiParameter('unread', OpenApiTypes.BOOL), OpenApiParameter('limit', int),
                OpenApiParameter('offset', int)],
    responses=inline_serializer('NotificationInbox', {
        'count': s.IntegerField(), 'unread_count': s.IntegerField(),
        'results': inline_serializer('NotificationItem', {
            'id': s.UUIDField(), 'title': s.CharField(), 'message': s.CharField(), 'channel': s.CharField(),
            'status': s.CharField(), 'is_read': s.BooleanField(), 'created_at': s.DateTimeField(),
            'sent_at': s.DateTimeField(allow_null=True)}, many=True)}),
    description='The caller\'s own notifications, newest first; limit 50 by default, at most 200.')
mark_read = extend_schema(
    request=inline_serializer('MarkReadRequest', {'ids': s.ListField(
        child=s.UUIDField(), required=False, help_text='Leave out to mark everything read')}),
    responses=inline_serializer('MarkReadResult', {'marked_read': s.IntegerField()}))
