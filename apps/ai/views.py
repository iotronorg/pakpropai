import logging
from datetime import datetime, timezone

from django.db.models import Sum, Count, Q
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminUser, IsAdminOrOrgAdmin
from apps.ai.models import TokenUsageRecord

logger = logging.getLogger(__name__)

_USD_PER_1K_IN = 0.000125   # Gemini 1.5 Flash input
_USD_PER_1K_OUT = 0.000375  # Gemini 1.5 Flash output


def _estimated_usd(tokens_in: int, tokens_out: int) -> float:
    return round(
        (tokens_in / 1000) * _USD_PER_1K_IN + (tokens_out / 1000) * _USD_PER_1K_OUT,
        6,
    )


class TokenUsageStatsView(APIView):
    """
    GET /ai/token-usage/
    Admin: all orgs (optional ?org=<uuid> filter)
    Developer: own org only
    Agent: 403
    """
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        user = request.user
        qs = TokenUsageRecord.objects.all()

        if user.role == 'developer':
            from apps.organizations.models import Membership
            try:
                membership = Membership.objects.select_related('organization').get(
                    user=user, organization__is_active=True
                )
                qs = qs.filter(org=membership.organization)
            except Exception:
                return Response({'detail': 'Organization not found.'}, status=404)
        elif user.role == 'admin':
            org_filter = request.query_params.get('org')
            if org_filter:
                qs = qs.filter(org_id=org_filter)
        else:
            return Response({'detail': 'Forbidden.'}, status=403)

        # Date range filters
        start = request.query_params.get('start')
        end = request.query_params.get('end')
        if start:
            try:
                qs = qs.filter(timestamp__gte=datetime.fromisoformat(start).replace(tzinfo=timezone.utc))
            except ValueError:
                pass
        if end:
            try:
                qs = qs.filter(timestamp__lte=datetime.fromisoformat(end).replace(tzinfo=timezone.utc))
            except ValueError:
                pass

        intent_filter = request.query_params.get('intent')
        if intent_filter:
            qs = qs.filter(intent=intent_filter)

        model_filter = request.query_params.get('model')
        if model_filter:
            qs = qs.filter(model=model_filter)

        agg = qs.aggregate(
            total_tokens_in=Sum('tokens_in'),
            total_tokens_out=Sum('tokens_out'),
            total_calls=Count('id'),
            cache_hits=Count('id', filter=Q(cache_hit=True)),
        )
        total_in = agg['total_tokens_in'] or 0
        total_out = agg['total_tokens_out'] or 0
        total_calls = agg['total_calls'] or 0
        cache_hits = agg['cache_hits'] or 0
        cache_hit_rate = round((cache_hits / total_calls * 100) if total_calls else 0, 2)

        # Savings: cache hits avoided LLM cost; estimate avg tokens as mean of non-cache records
        non_cache_qs = qs.filter(cache_hit=False)
        nc_agg = non_cache_qs.aggregate(
            nc_in=Sum('tokens_in'), nc_out=Sum('tokens_out'), nc_count=Count('id')
        )
        avg_in = (nc_agg['nc_in'] or 0) / max(nc_agg['nc_count'] or 1, 1)
        avg_out = (nc_agg['nc_out'] or 0) / max(nc_agg['nc_count'] or 1, 1)
        estimated_savings_usd = _estimated_usd(int(avg_in * cache_hits), int(avg_out * cache_hits))

        return Response({
            'total_tokens_in': total_in,
            'total_tokens_out': total_out,
            'total_calls': total_calls,
            'cache_hits': cache_hits,
            'cache_hit_rate': cache_hit_rate,
            'estimated_usd_cost': _estimated_usd(total_in, total_out),
            'estimated_savings_usd': estimated_savings_usd,
        })


class TokenBudgetStatusView(APIView):
    """
    GET /ai/token-budget/
    Developer: own org. Admin: ?org=<uuid> param required.
    """
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        from apps.ai.token_governor import SlidingWindowTokenBudget

        user = request.user
        if user.role == 'developer':
            from apps.organizations.models import Membership
            try:
                membership = Membership.objects.select_related('organization').get(
                    user=user, organization__is_active=True
                )
                org = membership.organization
            except Exception:
                return Response({'detail': 'Organization not found.'}, status=404)
        elif user.role == 'admin':
            org_id = request.query_params.get('org')
            if not org_id:
                return Response({'detail': 'admin must supply ?org= param.'}, status=400)
            from apps.organizations.models import Organization
            try:
                org = Organization.objects.get(id=org_id)
            except Organization.DoesNotExist:
                return Response({'detail': 'Organization not found.'}, status=404)
        else:
            return Response({'detail': 'Forbidden.'}, status=403)

        status = SlidingWindowTokenBudget.check_budget(str(org.id), org)
        return Response({
            'used': status.used,
            'limit': status.limit,
            'percent': status.percent,
            'state': status.state,
        })
