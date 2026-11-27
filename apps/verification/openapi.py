"""
OpenAPI descriptions of the verification endpoints (T-1091, KI-037).

The views are plain ``APIView``s that build their answers by hand, so the
generator cannot guess them; these serializers describe what they return and
are used only for the schema. Keep them in step with ``views.py``.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from .serializers import DocumentScanSerializer, VerificationSerializer

ERROR = OpenApiResponse(description='Refused; `error`/`detail` and `error_info` (ADR-017)')
DENIED = {403: ERROR}


def page_of(name: str, item):
    return inline_serializer(name, {'count': serializers.IntegerField(), 'results': item})


ALERT = {
    'id': serializers.CharField(), 'type': serializers.ChoiceField(['document_scan', 'verification', 'property']),
    'severity': serializers.CharField(help_text='high, medium or low'), 'phone': serializers.CharField(),
    'detail': serializers.CharField(), 'owner': serializers.CharField(allow_blank=True),
    'created_at': serializers.DateTimeField(),
}
BLACKLIST_ENTRY = {
    'id': serializers.IntegerField(), 'token': serializers.CharField(), 'reason': serializers.CharField(),
    'added_by': serializers.CharField(), 'expires_at': serializers.DateTimeField(allow_null=True),
    'created_at': serializers.DateTimeField(),
}
FLAGGED_USER = {
    'phone': serializers.CharField(), 'reason': serializers.CharField(),
    'flags': serializers.ListField(child=serializers.CharField()),
    'risk': serializers.CharField(help_text='high, medium or low'), 'last_seen': serializers.DateTimeField(),
}

fraud_check = extend_schema(
    request=inline_serializer('FraudCheckRequest', {'query': serializers.CharField(max_length=500)}),
    responses={200: OpenApiResponse(OpenApiTypes.OBJECT, description='FraudCheckService result'), 400: ERROR})
queue = extend_schema(
    parameters=[OpenApiParameter('status', str)],
    responses={200: page_of('VerificationPage', VerificationSerializer(many=True)), **DENIED})
review = extend_schema(
    request=inline_serializer('VerificationReviewRequest', {
        'status': serializers.ChoiceField(['passed', 'failed', 'disputed']),
        'notes': serializers.CharField(required=False)}),
    responses={200: VerificationSerializer, 400: ERROR, 404: ERROR})
scans = extend_schema(operation_id='verification_documents_list', parameters=[OpenApiParameter(n, str) for n in ('verification', 'document_type', 'status')],
                      responses={200: page_of('DocumentScanPage', DocumentScanSerializer(many=True)), **DENIED})
scan_detail = extend_schema(responses={200: DocumentScanSerializer, 403: ERROR, 404: ERROR})
link_scan = extend_schema(request=None, responses={200: VerificationSerializer, 404: ERROR})
fraud_stats = extend_schema(responses={200: inline_serializer('FraudStats', {
    name: serializers.IntegerField() for name in (
        'suspicious_scans', 'red_flag_scans', 'fraud_verifications', 'disputed_properties',
        'high_risk_properties', 'blacklisted_tokens', 'alerts_last_7_days')})})
fraud_alerts = extend_schema(parameters=[OpenApiParameter('limit', int, description='1–200, default 50')],
                             responses={200: page_of('FraudAlertPage', inline_serializer('FraudAlert', ALERT, many=True))})
blacklist_list = extend_schema(responses={200: page_of('FraudBlacklistPage', inline_serializer('FraudBlacklistEntry', BLACKLIST_ENTRY, many=True))})
blacklist_add = extend_schema(
    request=inline_serializer('FraudBlacklistAddRequest', {
        'token': serializers.CharField(), 'reason': serializers.CharField(required=False),
        'ttl_days': serializers.IntegerField(required=False, max_value=3650,
                                             help_text='0 or absent: never expires')}),
    responses={(201, 'application/json'): inline_serializer('FraudBlacklistAdded', {
        'id': serializers.IntegerField(), 'token': serializers.CharField(), 'reason': serializers.CharField(),
        'expires_at': serializers.DateTimeField(allow_null=True), 'created': serializers.BooleanField()}),
        400: ERROR}, description='200 instead of 201 when the token was already listed (updated).')
blacklist_delete = extend_schema(responses={204: None, 404: ERROR})
flagged_users = extend_schema(responses={200: page_of('FlaggedUserPage', inline_serializer('FlaggedUser', FLAGGED_USER, many=True))})
bulk_reject = extend_schema(
    request=inline_serializer('BulkRejectRequest', {
        'verification_ids': serializers.ListField(child=serializers.CharField()),
        'notes': serializers.CharField(required=False)}),
    responses={200: inline_serializer('BulkRejectResult', {'rejected': serializers.IntegerField()}), 400: ERROR})
certificate = extend_schema(responses={
    200: inline_serializer('TrustCertificate', {
        'property_id': serializers.CharField(), 'certificate_url': serializers.URLField(),
        'verified_at': serializers.DateTimeField(allow_null=True), 'signal_score': serializers.IntegerField(allow_null=True)}),
    202: OpenApiResponse(description='Certificate is being generated'), 403: ERROR, 404: ERROR})
id_session = extend_schema(
    request=inline_serializer('IDVerificationRequest', {
        'doc_type': serializers.CharField(required=False, default='passport'),
        'redirect_url': serializers.URLField(required=False)}),
    responses={201: inline_serializer('IDVerificationSession', {
        'supported': serializers.BooleanField(), 'provider': serializers.CharField(),
        'session_id': serializers.CharField(), 'session_url': serializers.URLField()}),
        400: OpenApiResponse(description='Not available in this market (`supported: false`)'),
        502: ERROR, 503: ERROR})
# Called by the identity providers, never by clients; their contract is the provider's.
provider_callback = extend_schema(exclude=True)
