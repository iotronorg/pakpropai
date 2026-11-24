"""
AI cost report (T-2011, MVP acceptance 9, NFR-COST-001): real token cost per
conversation and per qualified lead, from ``AITrace``.
"""
from datetime import datetime, time, timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminOrOrgAdmin

from .models import AITrace
from .views import _scope_org

MAX_DAYS = 366


class CostRowSerializer(serializers.Serializer):
    currency = serializers.CharField()
    total = serializers.DecimalField(max_digits=18, decimal_places=8)
    per_conversation = serializers.DecimalField(max_digits=18, decimal_places=8, allow_null=True)
    per_qualified_lead = serializers.DecimalField(max_digits=18, decimal_places=8, allow_null=True)


class DayRowSerializer(serializers.Serializer):
    date = serializers.DateField()
    currency = serializers.CharField(allow_blank=True)
    turns = serializers.IntegerField()
    cost = serializers.DecimalField(max_digits=18, decimal_places=8, allow_null=True)


class AICostReportSerializer(serializers.Serializer):
    start = serializers.DateField()
    end = serializers.DateField()
    turns = serializers.IntegerField()
    model_turns = serializers.IntegerField()
    conversations = serializers.IntegerField()
    qualified_leads = serializers.IntegerField()
    tokens_in = serializers.IntegerField()
    tokens_out = serializers.IntegerField()
    cached_tokens = serializers.IntegerField()
    unpriced_turns = serializers.IntegerField(help_text='> 0 means the costs below are incomplete')
    fallback_turns = serializers.IntegerField()
    guard_blocked_turns = serializers.IntegerField()
    grounding_blocked_turns = serializers.IntegerField()
    failed_turns = serializers.IntegerField()
    costs = CostRowSerializer(many=True)
    by_day = DayRowSerializer(many=True)


def _range(params):
    out = {}
    today = timezone.now().date()
    for name, default in (('end', today), ('start', None)):
        raw = params.get(name)
        try:
            value = parse_date(raw) if raw else default or out['end'] - timedelta(days=29)
        except ValueError:
            value = None
        if value is None:
            raise ValidationError({name: 'Use YYYY-MM-DD.'})
        out[name] = value
    if not 0 <= (out['end'] - out['start']).days < MAX_DAYS:
        raise ValidationError({'start': f'The range must be 1 to {MAX_DAYS} days.'})
    return out['start'], out['end']


def _per(total, n):
    return round(total / n, 8) if n else None


class AICostReportView(APIView):
    """Org owner/admin: the active org. Platform admin: everything, or ?org=<uuid>. Days are UTC."""
    permission_classes = [IsAdminOrOrgAdmin]

    @extend_schema(responses=AICostReportSerializer, parameters=[
        OpenApiParameter('start', str, description='YYYY-MM-DD (default: 30 days)'),
        OpenApiParameter('end', str, description='YYYY-MM-DD, inclusive'),
        OpenApiParameter('org', str, description='Platform admins only'),
    ])
    def get(self, request):
        from datetime import timezone as dt_timezone

        from apps.leads.models import Lead
        start, end = _range(request.query_params)
        org = _scope_org(request)
        window = {'created_at__gte': datetime.combine(start, time.min, dt_timezone.utc),
                  'created_at__lt': datetime.combine(end + timedelta(days=1), time.min, dt_timezone.utc)}
        traces = AITrace.objects.filter(**window).order_by()
        leads = Lead.objects.filter(status=Lead.Status.QUALIFIED, is_sandbox=False, **window)
        if org is not None:
            traces, leads = traces.filter(organization=org), leads.filter(organization=org)
        agg = traces.aggregate(
            turns=Count('id'), model_turns=Count('id', filter=Q(llm_calls__gt=0)),
            conversations=Count('conversation_ref', distinct=True,
                                filter=Q(interactive=True) & ~Q(conversation_ref='')),
            tokens_in=Sum('tokens_in', default=0), tokens_out=Sum('tokens_out', default=0),
            cached_tokens=Sum('cached_tokens', default=0),
            unpriced_turns=Count('id', filter=Q(pricing=AITrace.Pricing.UNPRICED)),
            fallback_turns=Count('id', filter=Q(fallback_used=True)),
            guard_blocked_turns=Count('id', filter=~Q(guard_results=[])),
            grounding_blocked_turns=Count('id', filter=Q(grounding_result='blocked')),
            failed_turns=Count('id', filter=~Q(error='')))
        qualified = leads.count()
        priced = traces.filter(pricing=AITrace.Pricing.PRICED)
        costs = [{'currency': row['currency'], 'total': row['total'],
                  'per_conversation': _per(row['total'], agg['conversations']),
                  'per_qualified_lead': _per(row['total'], qualified)}
                 for row in priced.values('currency').annotate(total=Sum('cost')).order_by('currency')]
        by_day = (traces.annotate(date=TruncDate('created_at', tzinfo=dt_timezone.utc))
                  .values('date', 'currency').annotate(turns=Count('id'), cost=Sum('cost'))
                  .order_by('date', 'currency'))
        return Response(AICostReportSerializer({
            'start': start, 'end': end, 'qualified_leads': qualified, 'costs': costs,
            'by_day': list(by_day), **agg}).data)
