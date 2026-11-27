"""
OpenAPI descriptions of the compliance endpoints (T-1091, KI-037).

The views are plain ``APIView``s that answer with ``.values()`` rows or hand-built
dicts; these describe them for the schema only. Keep them in step with ``views.py``.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')
GDPR_ONLY = OpenApiResponse(description='Not available: the organization is not in a GDPR jurisdiction (KI-112)')
CSV = {(200, 'text/csv'): OpenApiTypes.BINARY}

SCREENING = {
    'screening_id': s.UUIDField(), 'screened_name': s.CharField(), 'id_number_prefix': s.CharField(),
    'list_source': s.CharField(), 'match_type': s.CharField(), 'risk_score': s.IntegerField(),
    'deal_lock_id': s.UUIDField(allow_null=True), 'org_id': s.UUIDField(allow_null=True), 'status': s.CharField(),
    'screened_at': s.DateTimeField(),
}
SANCTION = {
    'id': s.UUIDField(), 'name': s.CharField(), 'id_number_prefix': s.CharField(), 'id_type': s.CharField(),
    'list_source': s.CharField(), 'risk_level': s.CharField(), 'org_id': s.UUIDField(allow_null=True),
    'is_active': s.BooleanField(), 'created_at': s.DateTimeField(),
}
PRIVACY_LOG = {
    'id': s.UUIDField(), 'action': s.CharField(), 'actor_id': s.CharField(allow_null=True),
    'subject_identifier': s.CharField(), 'jurisdiction': s.CharField(), 'regulation': s.CharField(),
    'details': s.JSONField(), 'is_sensitive': s.BooleanField(), 'created_at': s.DateTimeField(),
}
ORG_FILTER = OpenApiParameter('org', str, description='Platform admins only: one organization')

consent = extend_schema(
    request=inline_serializer('ConsentRequest', {'purpose': s.CharField(), 'revoke': s.BooleanField(default=False)}),
    responses={200: inline_serializer('ConsentState', {'purpose': s.CharField(), 'is_active': s.BooleanField()}),
               400: ERROR, 404: GDPR_ONLY})
export_status = extend_schema(operation_id='compliance_export_status', responses=inline_serializer('DataExportStatus', {
    'id': s.UUIDField(), 'status': s.CharField(), 'requested_at': s.DateTimeField(),
    'expires_at': s.DateTimeField(allow_null=True)}))
deletion = extend_schema(responses={
    202: inline_serializer('DataDeletionQueued', {'id': s.UUIDField(), 'status': s.CharField()}),
    404: GDPR_ONLY, 409: ERROR})
privacy_log = extend_schema(
    parameters=[OpenApiParameter('action', str), OpenApiParameter('jurisdiction', str)],
    responses={200: inline_serializer('PrivacyAuditEntry', PRIVACY_LOG, many=True), 403: ERROR},
    description='At most 200 rows, newest first.')
privacy_export = extend_schema(parameters=[ORG_FILTER], responses=CSV)
rtbf = extend_schema(
    request=inline_serializer('RtbfRequest', {'phone_e164': s.CharField(), 'reason': s.CharField(required=False)}),
    responses={202: inline_serializer('RtbfQueued', {'request_id': s.UUIDField()}), 400: ERROR})
rtbf_status = extend_schema(responses={200: inline_serializer('RtbfStatus', {
    'id': s.UUIDField(), 'status': s.CharField(), 'regulation': s.CharField(), 'request_source': s.CharField(),
    'requested_at': s.DateTimeField(), 'completed_at': s.DateTimeField(allow_null=True),
    'erasure_scope': s.JSONField()}), 403: ERROR, 404: ERROR})
pii_summary = extend_schema(responses={200: OpenApiResponse(
    OpenApiTypes.OBJECT, description='Pattern name → number of detections in the last 24 hours (integers)')})
screenings = extend_schema(
    operation_id='compliance_screenings_list',
    parameters=[OpenApiParameter('status', str), OpenApiParameter('date_from', OpenApiTypes.DATE)],
    responses={200: inline_serializer('ScreeningResult', SCREENING, many=True), 403: ERROR},
    description='At most 200 rows, newest first.')
screening = extend_schema(responses={200: inline_serializer('ScreeningResultDetail', SCREENING), 403: ERROR, 404: ERROR})
sanctions = extend_schema(responses=inline_serializer('SanctionRecord', SANCTION, many=True),
                          description='Active records, at most 500.')
sanction_add = extend_schema(
    request=inline_serializer('SanctionRecordCreate', {
        'name': s.CharField(), 'id_number': s.CharField(required=False, help_text='Stored as prefix + hash only'),
        'id_type': s.CharField(required=False), 'list_source': s.CharField(required=False, default='LOCAL'),
        'risk_level': s.CharField(required=False, default='high')}),
    responses={201: inline_serializer('SanctionRecordCreated', {'id': s.UUIDField()}), 400: ERROR})
sanction_update = extend_schema(
    request=inline_serializer('SanctionRecordUpdate', {
        name: s.CharField(required=False) for name in ('name', 'list_source', 'risk_level', 'id_type')}),
    responses={200: inline_serializer('SanctionRecordState', {'id': s.UUIDField(), 'is_active': s.BooleanField()}),
               404: ERROR})
sanction_delete = extend_schema(responses={204: None, 404: ERROR}, description='Deactivates the record.')
screenings_export = extend_schema(parameters=[ORG_FILTER], responses=CSV)
