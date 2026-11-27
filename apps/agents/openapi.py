"""
OpenAPI descriptions of the agent endpoints (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts or serializers; these
describe them for the schema only. Keep them in step with ``views.py``.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

from .serializers import AgentRegistrationSerializer, AgentSerializer

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')
DETAIL = inline_serializer('AgentActionResult', {'detail': s.CharField()})


def counts():
    return s.DictField(child=s.IntegerField(), help_text='value → count')


approve = extend_schema(request=None, responses={200: DETAIL, 400: ERROR, 402: ERROR, 403: ERROR, 404: ERROR},
                        description='Admins: any applicant; organization owners: their own. 402 when out of agent seats.')
reject = extend_schema(
    request=inline_serializer('AgentRejectRequest', {'rejection_reason': s.CharField()}),
    responses={200: DETAIL, 400: ERROR, 403: ERROR, 404: ERROR})
availability = extend_schema(
    request=inline_serializer('AgentAvailabilityRequest', {
        'availability_status': s.CharField(help_text='available | busy | offline')}),
    responses={200: inline_serializer('AgentAvailability', {'availability_status': s.CharField()}),
               400: ERROR, 403: ERROR, 404: ERROR},
    description='`me/`: the caller\'s own profile; `{id}/`: platform admins only.')
companies_house = extend_schema(
    request=inline_serializer('CompaniesHouseVerifyRequest', {'companies_house_number': s.CharField()}),
    responses={200: inline_serializer('CompaniesHouseVerifyResult', {
        'companies_house_number': s.CharField(required=False), 'company_name': s.CharField(required=False),
        'is_active': s.BooleanField(required=False), 'verified': s.BooleanField(required=False),
        'skipped': s.BooleanField(required=False, help_text='True outside GB organisations'),
        'detail': s.CharField()}), 400: ERROR, 403: ERROR, 502: ERROR})
leaderboard = extend_schema(responses={200: inline_serializer('AgentLeaderboard', {
    'count': s.IntegerField(), 'results': inline_serializer('AgentLeaderboardRow', {
        'rank': s.IntegerField(), 'agent_id': s.IntegerField(), 'name': s.CharField(),
        'total_leads': s.IntegerField(), 'conversion_rate': s.FloatField(help_text='Percent'),
        'avg_response_time_hours': s.FloatField(allow_null=True), 'closed_deals': s.IntegerField(),
        'rating': s.FloatField()}, many=True)}), 403: ERROR})
register = extend_schema(request=AgentRegistrationSerializer, responses={201: inline_serializer('AgentRegistered', {
    'detail': s.CharField(), 'agent_id': s.IntegerField(), 'otp_required': s.BooleanField(),
    'phone': s.CharField()}), 400: ERROR, 429: ERROR})
stats = extend_schema(
    parameters=[OpenApiParameter('period', str, enum=['weekly', 'monthly'], description='Adds `trend`')],
    responses={200: inline_serializer('AgentStats', {
        'agent_id': s.IntegerField(), 'name': s.CharField(), 'availability_status': s.CharField(),
        'total_leads': s.IntegerField(), 'closed_leads': s.IntegerField(), 'conversion_rate': s.FloatField(),
        'closed_deals': s.IntegerField(), 'avg_response_time_hours': s.FloatField(allow_null=True),
        'rating': s.FloatField(), 'is_verified': s.BooleanField(), 'by_status': counts(), 'by_source': counts(),
        'trend': inline_serializer('AgentStatsPeriod', {'period': s.CharField(), 'count': s.IntegerField()},
                                   many=True, required=False)}), 403: ERROR, 404: ERROR})
FREELANCE = inline_serializer('FreelanceProfile', {
    'id': s.IntegerField(), 'license_number': s.CharField(allow_blank=True), 'verification_status': s.CharField(),
    'global_rating': s.CharField(allow_null=True), 'created_at': s.DateTimeField()})
freelance = extend_schema(
    request=inline_serializer('FreelanceProfileRequest', {'license_number': s.CharField(required=False)}),
    responses={200: FREELANCE, 201: FREELANCE, 403: ERROR},
    description='Creates (201) or updates (200) the caller\'s profile; agents only.')
team = extend_schema(operation_id='agents_team_list', responses={200: AgentSerializer(many=True), 403: ERROR})
team_add = extend_schema(request=inline_serializer('TeamAddRequest', {'agent_id': s.IntegerField()}),
                         responses={200: AgentSerializer, 400: ERROR, 402: ERROR, 403: ERROR, 404: ERROR},
                         description='Adds an unaffiliated agent; 402 when out of agent seats.')
team_remove = extend_schema(responses={204: None, 403: ERROR, 404: ERROR})
