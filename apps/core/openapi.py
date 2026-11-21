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
