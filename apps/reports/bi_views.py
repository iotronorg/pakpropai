"""
Enterprise BI analytics views.

Three endpoints that delegate all computation to analytics_engine.py:
  GET /reports/funnel/       — 6-stage sales funnel + conversion rates
  GET /reports/wa-tokens/    — last 6 months WA AI token usage
  GET /reports/leaderboard/  — agent speed leaderboard (Redis sorted set)
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminOrOrgAdmin, get_user_org
from .analytics_engine import (
    compute_funnel,
    compute_wa_token_usage,
    get_agent_speed_leaderboard,
    refresh_leaderboard,
)


def _org_or_403(request):
    """Return (org, None) or (None, 403 Response)."""
    from rest_framework import status as drf_status

    if request.user.role == 'admin':
        org_id = request.query_params.get('org')
        if not org_id:
            return None, Response(
                {'detail': 'admin must supply ?org=<uuid>'},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        from apps.organizations.models import Organization
        try:
            import uuid
            return Organization.objects.get(id=uuid.UUID(org_id)), None
        except (Organization.DoesNotExist, ValueError):
            return None, Response(
                {'detail': 'Organization not found.'},
                status=drf_status.HTTP_404_NOT_FOUND,
            )

    org = get_user_org(request.user)
    if org is None:
        return None, Response(
            {'detail': 'No organization found for this user.'},
            status=drf_status.HTTP_403_FORBIDDEN,
        )
    return org, None


class FunnelAnalyticsView(APIView):
    """GET /reports/funnel/ — 6-stage sales funnel conversion rates."""
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        org, err = _org_or_403(request)
        if err:
            return err
        return Response(compute_funnel(org))


class WaTokenUsageView(APIView):
    """GET /reports/wa-tokens/ — rolling 6-month WhatsApp AI token usage."""
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        org, err = _org_or_403(request)
        if err:
            return err
        return Response(compute_wa_token_usage(org))


class AgentSpeedLeaderboardView(APIView):
    """GET /reports/leaderboard/ — top agents ranked by response speed."""
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        org, err = _org_or_403(request)
        if err:
            return err
        try:
            top_n = min(int(request.query_params.get('top_n', 10)), 50)
        except (ValueError, TypeError):
            top_n = 10

        refresh = request.query_params.get('refresh') == '1'
        if refresh:
            refresh_leaderboard(org)

        return Response(get_agent_speed_leaderboard(org, top_n))
