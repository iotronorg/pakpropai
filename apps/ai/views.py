import logging
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminOrOrgAdmin, get_admin_org
from . import openapi as API
from apps.ai.models import TokenUsageRecord

logger = logging.getLogger(__name__)


def _scope_org(request, *, required=False):
    """The org whose AI usage the caller may read; None = whole platform (admins only)."""
    if request.user.role != 'admin':
        return get_admin_org(request.user)       # IsAdminOrOrgAdmin guarantees one
    raw = request.query_params.get('org')
    if not raw:
        if required:
            raise ValidationError({'org': 'admin must supply ?org= param.'})
        return None
    from apps.organizations.models import Organization
    try:
        return Organization.objects.get(id=raw)
    except (Organization.DoesNotExist, DjangoValidationError, ValueError):
        raise NotFound('Organization not found.') from None


def _estimated_usd(per_model) -> float | None:
    """Cost at today's ModelPrice; None when any model used has no USD price."""
    from apps.ai.tracing import call_cost, price_for
    total = Decimal(0)
    for row in per_model:
        tokens_in, tokens_out = row['tin'] or 0, row['tout'] or 0
        if not tokens_in and not tokens_out:
            continue
        price = price_for(row['model'], timezone.now().date())
        if price is None or price.currency != 'USD':
            return None
        total += call_cost(price, tokens_in, tokens_out)
    return round(float(total), 6)


class TokenUsageStatsView(APIView):
    """
    GET /ai/token-usage/
    Platform admin: all orgs (optional ?org=<uuid>). Org owner/admin: the active org.
    Costs are null when a model has no ModelPrice (T-2011).
    """
    permission_classes = [IsAdminOrOrgAdmin]

    @API.token_usage
    def get(self, request):
        qs = TokenUsageRecord.objects.all()
        org = _scope_org(request)
        if org is not None:
            qs = qs.filter(org=org)

        # Date range filters
        start = request.query_params.get('start')
        end = request.query_params.get('end')
        if start:
            try:
                qs = qs.filter(timestamp__gte=datetime.fromisoformat(start).replace(tzinfo=dt_timezone.utc))
            except ValueError:
                pass
        if end:
            try:
                qs = qs.filter(timestamp__lte=datetime.fromisoformat(end).replace(tzinfo=dt_timezone.utc))
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

        per_model = list(qs.filter(cache_hit=False).order_by().values('model').annotate(
            tin=Sum('tokens_in'), tout=Sum('tokens_out'), n=Count('id')))
        cost = _estimated_usd(per_model)
        model_calls = sum(row['n'] for row in per_model)
        # A cache hit saves about one average model turn.
        savings = round(cost / model_calls * cache_hits, 6) if cost is not None and model_calls else None

        return Response({
            'total_tokens_in': total_in,
            'total_tokens_out': total_out,
            'total_calls': total_calls,
            'cache_hits': cache_hits,
            'cache_hit_rate': cache_hit_rate,
            'estimated_usd_cost': cost,
            'estimated_savings_usd': savings,
        })


class TokenBudgetStatusView(APIView):
    """
    GET /ai/token-budget/
    Org owner/admin: the active org. Platform admin: ?org=<uuid> required.
    """
    permission_classes = [IsAdminOrOrgAdmin]

    @API.token_budget
    def get(self, request):
        from apps.ai.token_governor import SlidingWindowTokenBudget

        org = _scope_org(request, required=True)
        status = SlidingWindowTokenBudget.check_budget(str(org.id), org)
        return Response({
            'used': status.used,
            'limit': status.limit,
            'percent': status.percent,
            'state': status.state,
        })
