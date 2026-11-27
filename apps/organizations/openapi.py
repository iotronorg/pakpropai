"""
OpenAPI descriptions of the organization endpoints (T-1091, KI-037).

Plain ``APIView``s answering with serializers or hand-built dicts; these
describe them for the schema only. Keep them in step with ``views.py``,
``external_api.py`` and ``theme_views.py``.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

from .serializers import (
    OrganizationDetailSerializer, OrganizationListSerializer, OrgRegistrationOTPVerifySerializer,
    OrgRegistrationSerializer,
)

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')


def counts():
    return s.DictField(child=s.IntegerField(), help_text='value → count')


# ── Organizations ─────────────────────────────────────────────────────────────

org_list = extend_schema(
    operation_id='organizations_list',
    parameters=[OpenApiParameter('search', str), OpenApiParameter('is_active', OpenApiTypes.BOOL),
                OpenApiParameter('page', int)],
    responses={200: inline_serializer('OrganizationPage', {
        'count': s.IntegerField(), 'next': s.URLField(allow_null=True), 'previous': s.URLField(allow_null=True),
        'results': OrganizationListSerializer(many=True)}), 403: ERROR},
    description='Platform admins: all; organization owners: the organizations they own. 20 per page.')
org_create = extend_schema(request=OrganizationDetailSerializer,
                           responses={201: OrganizationDetailSerializer, 400: ERROR, 403: ERROR})
org_detail = extend_schema(responses={200: OrganizationDetailSerializer, 403: ERROR, 404: ERROR})
org_update = extend_schema(request=OrganizationDetailSerializer,
                           responses={200: OrganizationDetailSerializer, 400: ERROR, 403: ERROR, 404: ERROR},
                           description='Only platform admins may change the owner or the verified flag.')
org_delete = extend_schema(responses={204: None, 403: ERROR, 404: ERROR})
me = extend_schema(operation_id='organizations_me_retrieve',
                   responses={200: OrganizationDetailSerializer, 403: ERROR, 404: ERROR})
me_update = extend_schema(request=OrganizationDetailSerializer,
                          responses={200: OrganizationDetailSerializer, 400: ERROR, 403: ERROR, 404: ERROR})
suspend = extend_schema(request=None, responses={200: OrganizationDetailSerializer, 400: ERROR, 404: ERROR})
activate = extend_schema(request=None, responses={200: OrganizationDetailSerializer, 400: ERROR, 404: ERROR})

# ── Feature flags ─────────────────────────────────────────────────────────────

FEATURES = s.DictField(child=s.BooleanField(), help_text='feature key → enabled (organization override or platform)')
CONFIG = inline_serializer('OrganizationConfig', {
    'features': FEATURES, 'overrides': s.ListField(child=s.CharField()),
    'allowed_keys': s.ListField(child=s.CharField())})
UPDATED = inline_serializer('OrganizationFeatures', {'features': FEATURES})
RESET = inline_serializer('OrganizationConfigReset', {'detail': s.CharField()})
config = extend_schema(responses={200: CONFIG, 403: ERROR, 404: ERROR})
config_update = extend_schema(request=OpenApiTypes.OBJECT, responses={200: UPDATED, 400: ERROR, 403: ERROR, 404: ERROR},
                              description='Body: `{allowed key: true|false}`; any other key → 400.')
config_reset = extend_schema(responses={200: RESET, 400: ERROR, 403: ERROR, 404: ERROR},
                             description='Back to the platform default.')

# ── Dashboards ────────────────────────────────────────────────────────────────

dashboard = extend_schema(responses={200: inline_serializer('OrganizationDashboard', {
    'leads': inline_serializer('OrganizationDashboardLeads', {
        **{n: s.IntegerField() for n in ('total', 'hot', 'new_this_week', 'routing_queue')},
        'by_status': counts(), 'by_intent': counts()}),
    'agents': inline_serializer('OrganizationDashboardAgents', {'active': s.IntegerField(), 'pending': s.IntegerField()}),
    'inventory': inline_serializer('OrganizationDashboardInventory', {
        'total': s.IntegerField(), 'verified': s.IntegerField(), 'avg_ai_score': s.FloatField(),
        'by_type': counts()})}), 403: ERROR, 404: ERROR})
ai_stats = extend_schema(responses={200: inline_serializer('OrganizationAIStats', {
    'summary': inline_serializer('OrganizationAIStatsSummary', {
        **{n: s.IntegerField() for n in ('total_leads', 'hot_leads', 'routing_queue', 'agent_assigned',
                                         'qualified_leads', 'new_this_week', 'leads_with_convos')},
        'chat_success_rate': s.FloatField(help_text='Percent')}),
    'recent_conversations': inline_serializer('OrganizationRecentConversation', {
        'lead_id': s.UUIDField(), 'lead_phone': s.CharField(allow_null=True,
                                                            help_text='Masked when the person withdrew consent'),
        'lead_name': s.CharField(allow_null=True), 'lead_score': s.IntegerField(), 'lead_status': s.CharField(),
        'message_preview': s.CharField(), 'channel': s.CharField(), 'created_at': s.DateTimeField()},
        many=True)}), 403: ERROR, 404: ERROR})

# ── Self-service registration ─────────────────────────────────────────────────

register = extend_schema(
    request=OrgRegistrationSerializer,
    responses={201: inline_serializer('OrgRegistrationStarted', {'otp_required': s.BooleanField(),
                                                                 'phone': s.CharField()}), 400: ERROR})


def _register_verify():
    from apps.users.openapi import LOGIN_NOTE, login
    from apps.users.serializers import UserSerializer
    return extend_schema(request=OrgRegistrationOTPVerifySerializer, description=LOGIN_NOTE,
                         responses={200: login('OrgRegistrationLoginResult', UserSerializer), 400: ERROR})


register_verify = _register_verify()


# ── Theme ─────────────────────────────────────────────────────────────────────

def _theme():
    from .serializers import OrganizationThemeSerializer
    return OrganizationThemeSerializer


public_theme = extend_schema(responses=_theme(), description=(
    'The tenant\'s palette, resolved from the Host header; platform defaults when none is set.'))
org_theme = extend_schema(responses={200: _theme(), 404: ERROR},
                          description='Platform defaults while the organization has no theme.')
org_theme_update = extend_schema(request=_theme(), responses={200: _theme(), 400: ERROR, 404: ERROR})


# ── API keys and the external API (external_api.py) ──────────────────────────
# The key serializers live in external_api.py, which imports this module: build lazily.

def _keys():
    from .external_api import ApiKeyCreateSerializer, ApiKeyListSerializer
    return ApiKeyCreateSerializer, ApiKeyListSerializer


def keys_list(view_method):
    return extend_schema(responses={200: _keys()[1](many=True), 403: ERROR})(view_method)


def keys_create(view_method):
    return extend_schema(request=_keys()[0], responses={201: inline_serializer('ApiKeyCreated', {
        'id': s.UUIDField(), 'name': s.CharField(), 'token': s.CharField(help_text='Shown exactly once'),
        'key_hint': s.CharField(), 'scopes': s.ListField(child=s.CharField()),
        'expires_at': s.DateTimeField(allow_null=True), 'warning': s.CharField()}), 400: ERROR, 403: ERROR})(view_method)


key_revoke = extend_schema(responses={204: None, 403: ERROR, 404: ERROR})


def external_leads(view_method):
    from apps.leads.serializers import LeadSerializer
    return extend_schema(operation_id='external_leads_list', responses=LeadSerializer(many=True),
                         description='Scope `leads:read`. The 100 newest.')(view_method)


def external_lead(view_method):
    from apps.leads.serializers import LeadSerializer
    return extend_schema(responses={200: LeadSerializer, 404: ERROR},
                         description='Scope `leads:read`; only this organization\'s leads.')(view_method)


def external_inventory(view_method):
    from apps.properties.serializers import PropertyListSerializer
    return extend_schema(responses=PropertyListSerializer(many=True),
                         description='Scope `inventory:read`. The 200 newest active listings.')(view_method)


def external_inventory_add(view_method):
    from apps.properties.serializers import PropertyCreateSerializer
    return extend_schema(request=PropertyCreateSerializer,
                         responses={201: inline_serializer('ExternalListingCreated', {'id': s.UUIDField()}),
                                    400: ERROR}, description='Scope `inventory:write`.')(view_method)
