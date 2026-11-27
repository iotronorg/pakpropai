"""
OpenAPI descriptions of the lead admin endpoints (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts; these describe them for the
schema only. Keep them in step with ``admin_views.py``.
"""
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')

bulk_assign = extend_schema(
    request=inline_serializer('BulkAssignLeadsRequest', {
        'lead_ids': s.ListField(child=s.UUIDField(), max_length=200), 'agent_id': s.IntegerField()}),
    responses={200: inline_serializer('BulkAssignLeadsResult', {
        'assigned': s.IntegerField(help_text='Leads newly assigned (already-assigned ones are skipped)'),
        'agent_id': s.IntegerField()}), 400: ERROR, 403: ERROR, 404: ERROR, 429: ERROR},
    description='Admins and organization owners; an owner\'s agent must work for the organization.')
duplicates = extend_schema(responses={200: inline_serializer('DuplicateLeadGroups', {
    'count': s.IntegerField(), 'results': inline_serializer('DuplicateLeadGroup', {
        'normalized_phone': s.CharField(), 'leads': inline_serializer('DuplicateLead', {
            'id': s.UUIDField(), 'phone': s.CharField(), 'status': s.CharField(), 'intent': s.CharField(),
            'created_at': s.DateTimeField()}, many=True)}, many=True)}), 403: ERROR},
    description='Platform admins only: leads whose phone numbers normalize to the same value.')
merge = extend_schema(
    request=inline_serializer('MergeLeadsRequest', {'primary_id': s.UUIDField(), 'secondary_id': s.UUIDField()}),
    responses={200: inline_serializer('MergeLeadsResult', {
        'detail': s.CharField(), 'primary_id': s.UUIDField(), 'messages_transferred': s.IntegerField()}),
        400: ERROR, 403: ERROR, 404: ERROR},
    description='Platform admins only. Moves messages, enquiries and appointments, then deletes the secondary.')
