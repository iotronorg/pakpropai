from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SystemConfig
from .services import SystemConfigService, SENTINEL


class IsAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'admin'


class ConfigView(APIView):
    """
    GET  /api/v1/config/  — returns full config; sensitive keys masked if set
    PATCH /api/v1/config/ — bulk update; sentinel values are skipped
    """
    permission_classes = [IsAdmin]

    def get(self, request):
        raw = SystemConfigService.get_all()
        data = {}
        for key, value in raw.items():
            if key in SystemConfig.SENSITIVE_KEYS:
                data[key] = SENTINEL if SystemConfigService.is_set(key) else ''
            else:
                data[key] = value

        missing = SystemConfigService.get_missing_required()
        data['setup_complete']   = len(missing) == 0
        data['missing_required'] = missing
        return Response(data)

    def patch(self, request):
        incoming = request.data
        to_save  = {}

        for key, value in incoming.items():
            if key not in SystemConfig.DEFAULTS:
                continue
            if value == SENTINEL:
                continue
            to_save[key] = value

        if to_save:
            SystemConfigService.bulk_set(to_save, user=request.user)

        # Return updated config (same shape as GET)
        return self.get(request)
