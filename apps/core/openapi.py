"""
OpenAPI schema support (T-1090a, ADR-016).

* Authentication extensions so the schema documents the two real auth
  schemes instead of warning about unknown classes.
* ``SchemaAccessPermission``: who may download the schema from the running
  service. The schema maps every endpoint, so by default only staff may read
  it; set ``OPENAPI_SCHEMA_PUBLIC=True`` to publish it (e.g. on staging for the
  frontend team). CI also generates it as a build artefact.

Imported from ``CoreConfig.ready()`` so drf-spectacular registers the
extensions before the first schema build.
"""
from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.permissions import BasePermission


class JWTCookieOrHeaderScheme(OpenApiAuthenticationExtension):
    target_class = 'apps.users.authentication.JWTCookieOrHeaderAuthentication'
    name = ['jwtBearer', 'jwtCookie']
    priority = 1

    def get_security_definition(self, auto_schema):
        return [
            {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT'},
            {
                'type': 'apiKey', 'in': 'cookie', 'name': 'access_token',
                'description': 'httpOnly cookie set by the login endpoints. '
                               'Unsafe methods also need the CSRF token.',
            },
        ]


class ApiKeyScheme(OpenApiAuthenticationExtension):
    target_class = 'apps.organizations.external_api.ApiKeyAuthentication'
    name = 'orgApiKey'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey', 'in': 'header', 'name': 'X-API-Key',
            'description': 'Organisation API key `rtk_<prefix>_<secret>`; '
                           'also accepted as `Authorization: Bearer`.',
        }


class SchemaAccessPermission(BasePermission):
    """Staff only, unless ``OPENAPI_SCHEMA_PUBLIC`` is enabled."""

    def has_permission(self, request, view):
        if getattr(settings, 'OPENAPI_SCHEMA_PUBLIC', False):
            return True
        user = request.user
        return bool(user and user.is_authenticated and (user.is_staff or getattr(user, 'role', '') == 'admin'))


# ── Descriptions of the core views (T-1091, KI-037); keep in step with ``views.py`` ──

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer  # noqa: E402
from rest_framework import serializers as _s  # noqa: E402

_HEALTH = inline_serializer('HealthCheck', {
    'status': _s.CharField(help_text='ok | degraded'), 'version': _s.CharField(),
    'checks': _s.DictField(child=_s.DictField(), help_text='database, redis, celery_broker, ai, whatsapp → '
                                                         '{status, latency_ms | detail | note, …}')})
health = extend_schema(auth=[], responses={200: _HEALTH, 503: _HEALTH},
                       description='Unauthenticated; for load balancers and start-up checks.')
audit_log = extend_schema(
    operation_id='audit_log_list',
    parameters=[OpenApiParameter('actor', str, description='Phone contains'), OpenApiParameter('action', str),
                OpenApiParameter('target_model', str), OpenApiParameter('target_id', str),
                OpenApiParameter('limit', int), OpenApiParameter('offset', int)],
    responses={200: inline_serializer('AuditLogPage', {
        'count': _s.IntegerField(), 'limit': _s.IntegerField(), 'offset': _s.IntegerField(),
        'results': inline_serializer('AuditLogEntry', {
            'id': _s.IntegerField(), 'actor_phone': _s.CharField(allow_null=True), 'action': _s.CharField(),
            'target_model': _s.CharField(), 'target_id': _s.CharField(), 'detail': _s.CharField(allow_blank=True),
            'before': _s.JSONField(allow_null=True), 'after': _s.JSONField(allow_null=True),
            'ip_address': _s.CharField(allow_null=True), 'created_at': _s.DateTimeField()}, many=True)}),
        403: OpenApiResponse(description='Platform admins only')},
    description='Limit 50 by default, at most 200; newest first.')
