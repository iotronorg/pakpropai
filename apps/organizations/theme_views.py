import logging

from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import IsAdminOrOrgAdmin, get_user_org
from .models import OrganizationTheme
from .serializers import OrganizationThemeSerializer

logger = logging.getLogger(__name__)

_PLATFORM_DEFAULTS = {
    'primary_color':   '#2563EB',
    'secondary_color': '#4F46E5',
    'accent_color':    '#0891B2',
    'logo_url':        '',
}

_THEME_TTL = 300  # seconds — matches TenantDomainMiddleware TTL


class ThemeConfigView(APIView):
    """
    GET /api/v1/theme/
    Public endpoint — no auth. Returns the active tenant's theme palette.
    Resolution: Redis cache → DB. Falls back to platform defaults.
    Tenant is resolved from the Host header by TenantDomainMiddleware.
    """
    permission_classes     = []
    authentication_classes = []

    def get(self, request):
        host = request.get_host().split(':')[0].lower()
        cache_key = f'theme_cfg:{host}'

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        org = getattr(request, 'tenant_org', None)
        if org is None:
            return Response(_PLATFORM_DEFAULTS)

        try:
            theme = org.theme
            data = OrganizationThemeSerializer(theme).data
        except OrganizationTheme.DoesNotExist:
            data = _PLATFORM_DEFAULTS

        cache.set(cache_key, data, _THEME_TTL)
        return Response(data)


class OrgThemeView(APIView):
    """
    GET  /api/v1/organizations/me/theme/  — return current theme or defaults
    PUT  /api/v1/organizations/me/theme/  — upsert theme; triggers cache invalidation via signal
    Developer / admin only.
    """
    permission_classes = [IsAuthenticated, IsAdminOrOrgAdmin]

    def _get_org(self, request):
        return get_user_org(request.user)

    def get(self, request):
        org = self._get_org(request)
        if org is None:
            return Response({'detail': 'No organization found.'}, status=404)
        try:
            theme = org.theme
            return Response(OrganizationThemeSerializer(theme).data)
        except OrganizationTheme.DoesNotExist:
            return Response(_PLATFORM_DEFAULTS)

    def put(self, request):
        org = self._get_org(request)
        if org is None:
            return Response({'detail': 'No organization found.'}, status=404)

        theme, _ = OrganizationTheme.objects.get_or_create(organization=org)
        serializer = OrganizationThemeSerializer(theme, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
