"""
AI ops console API (T-3090, GAP-103, ADR-061), under ``/api/v1/ai/ops/``.

Platform AI and ops admins read everything; only AI admins add model prices and
start eval runs. Kill switches stay at ``/api/v1/ai/kill-switches/`` and the
cost report at ``/api/v1/ai/costs/``.
"""
from decimal import Decimal, InvalidOperation

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAIAdmin, IsOpsAdmin
from apps.core.query_params import limit_offset

from . import ops

_PERIOD = [OpenApiParameter('from', str, description='First UTC day, YYYY-MM-DD (default: 6 days before "to")'),
           OpenApiParameter('to', str, description='Last UTC day (default: today)')]


class _OpsView(APIView):
    permission_classes = [IsAIAdmin | IsOpsAdmin]

    def period(self, request, default_days=7):
        try:
            return ops.window(request.query_params.get('from'), request.query_params.get('to'), default_days)
        except ValueError as exc:
            raise exceptions.ValidationError({'period': str(exc)}) from None

    def writer(self, request):
        if not IsAIAdmin().has_permission(request, self):
            raise exceptions.PermissionDenied('Only AI admins change this.')


class OverviewView(_OpsView):
    @extend_schema(parameters=_PERIOD, responses={200: OpenApiResponse(description='Turns by route, model, prompt; '
                                                                         'grounding, errors, latency, cost')})
    def get(self, request):
        from .models import AITrace
        since, until, first, last = self.period(request)
        qs = AITrace.objects.filter(created_at__gte=since, created_at__lt=until)
        return Response({'from': first, 'to': last, **ops.overview(qs)})


class PromptListView(_OpsView):
    @extend_schema(operation_id='ai_ops_prompts_list', parameters=_PERIOD,
                   responses={200: OpenApiResponse(description='Registered prompts with use')})
    def get(self, request):
        since, _, first, last = self.period(request, 30)
        return Response({'from': first, 'to': last, 'results': ops.prompts(since)})


class PromptDetailView(_OpsView):
    @extend_schema(responses={200: OpenApiResponse(description='Template and versions seen in use'),
                              404: OpenApiResponse(description='Unknown prompt')})
    def get(self, request, name):
        found = ops.prompt_detail(name)
        if found is None:
            raise exceptions.NotFound('Unknown prompt.')
        return Response(found)


class ModelListView(_OpsView):
    @extend_schema(parameters=_PERIOD, responses={200: OpenApiResponse(description='Providers in failover order')})
    def get(self, request):
        since, _, first, last = self.period(request)
        return Response({'from': first, 'to': last, 'results': ops.models(since)})


def _decimal(value, name, errors, *, required=True):
    if value in (None, ''):
        if required:
            errors[name] = 'Required.'
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        errors[name] = 'A number.'
        return None
    if not d.is_finite() or d < 0 or d > Decimal('1000000'):
        errors[name] = 'Between 0 and 1,000,000.'
        return None
    return d


class ModelPriceView(_OpsView):
    """Prices are history: a change is a new row from its effective day; rows are never edited."""

    @extend_schema(parameters=[OpenApiParameter('model', str)],
                   responses={200: OpenApiResponse(description='Price rows, newest first per model')})
    def get(self, request):
        from .models import ModelPrice
        qs = ModelPrice.objects.all()
        if (model := request.query_params.get('model')):
            qs = qs.filter(model=model[:80])
        return Response({'results': list(qs.values('id', 'model', 'effective_from', 'currency', 'input_per_mtok',
                                                   'cached_per_mtok', 'output_per_mtok')[:500])})

    @extend_schema(request=OpenApiTypes.OBJECT, responses={201: OpenApiResponse(description='Price added'),
                                                           400: OpenApiResponse(description='Field errors'),
                                                           409: OpenApiResponse(description='Same model and day')})
    def post(self, request):
        from datetime import date

        from django.db import IntegrityError, transaction

        from apps.core.audit import safe_record

        from .models import ModelPrice
        self.writer(request)
        d = request.data if isinstance(request.data, dict) else {}
        errors = {}
        model = str(d.get('model') or '').strip()
        if not model or len(model) > 80:
            errors['model'] = 'Required, at most 80 characters.'
        try:
            effective = date.fromisoformat(str(d.get('effective_from') or ''))
        except ValueError:
            errors['effective_from'] = 'YYYY-MM-DD.'
            effective = None
        currency = str(d.get('currency') or 'USD').upper()
        if not (len(currency) == 3 and currency.isalpha()):
            errors['currency'] = 'An ISO currency code.'
        prices = {k: _decimal(d.get(k), k, errors, required=k != 'cached_per_mtok')
                  for k in ('input_per_mtok', 'cached_per_mtok', 'output_per_mtok')}
        if errors:
            raise exceptions.ValidationError(errors)
        try:
            with transaction.atomic():
                row = ModelPrice.objects.create(model=model, effective_from=effective, currency=currency, **prices)
        except IntegrityError:
            return Response({'detail': 'This model already has a price from that day.', 'code': 'price.exists'},
                            status=status.HTTP_409_CONFLICT)
        safe_record('ai.model_price_added', category='ai', actor=request.user, target=row,
                    meta={'model': model, 'effective_from': effective.isoformat(), 'currency': currency,
                          **{k: str(v) if v is not None else None for k, v in prices.items()}})
        return Response({'id': row.id, 'model': row.model, 'effective_from': row.effective_from,
                         'currency': row.currency, **prices}, status=status.HTTP_201_CREATED)


class ToolListView(_OpsView):
    @extend_schema(parameters=_PERIOD, responses={200: OpenApiResponse(description='Tools, flags and calls')})
    def get(self, request):
        since, _, first, last = self.period(request)
        return Response({'from': first, 'to': last, 'results': ops.tools(since)})


class EvalRunListView(_OpsView):
    @extend_schema(operation_id='ai_ops_evals_list', responses={200: OpenApiResponse(description='Eval runs, newest first')})
    def get(self, request):
        from .models import AIEvalRun
        limit, offset = limit_offset(request.query_params, default=20, maximum=100)
        qs = AIEvalRun.objects.all()
        return Response({'count': qs.count(),
                         'results': [ops.eval_run(r) for r in qs[offset:offset + limit]]})

    @extend_schema(request=OpenApiTypes.OBJECT, responses={
        201: OpenApiResponse(description='The run, with failures per suite'),
        400: OpenApiResponse(description='Unknown suite or a live run asked for')})
    def post(self, request):
        self.writer(request)
        d = request.data if isinstance(request.data, dict) else {}
        if d.get('live'):
            raise exceptions.ValidationError({'live': 'Live runs call paid models; run `run_ai_evals --live`.'})
        suites = d.get('suites')
        if suites is not None and (not isinstance(suites, list) or not all(isinstance(s, str) for s in suites)):
            raise exceptions.ValidationError({'suites': 'A list of suite names.'})
        try:
            run = ops.run_evals(suites, user=request.user)
        except ValueError as exc:
            raise exceptions.ValidationError({'suites': str(exc)}) from None
        return Response(ops.eval_run(run, full=True), status=status.HTTP_201_CREATED)


class EvalRunDetailView(_OpsView):
    @extend_schema(responses={200: OpenApiResponse(description='One run with failures')})
    def get(self, request, pk):
        from .models import AIEvalRun
        run = AIEvalRun.objects.filter(pk=pk).first()
        if run is None:
            raise exceptions.NotFound('Eval run not found.')
        return Response(ops.eval_run(run, full=True))


class TraceListView(_OpsView):
    FILTERS = {'organization': 'organization_id', 'route': 'route', 'prompt': 'prompt_name', 'model': 'model',
               'provider': 'provider', 'grounding': 'grounding_result', 'error': 'error',
               'conversation': 'conversation_ref'}

    @extend_schema(operation_id='ai_ops_traces_list', parameters=_PERIOD + [OpenApiParameter(n, str) for n in FILTERS]
                   + [OpenApiParameter('fallback', bool), OpenApiParameter('limit', int),
                      OpenApiParameter('offset', int)],
                   responses={200: OpenApiResponse(description='Turns, newest first; no message text, no person')})
    def get(self, request):
        import uuid

        from .models import AITrace
        since, until, first, last = self.period(request)
        q = request.query_params
        qs = AITrace.objects.filter(created_at__gte=since, created_at__lt=until)
        for param, column in self.FILTERS.items():
            if (value := q.get(param)) is not None and value != '':
                if param == 'organization':
                    try:
                        value = uuid.UUID(value)
                    except ValueError:
                        raise exceptions.ValidationError({'organization': 'An organization id.'}) from None
                qs = qs.filter(**{column: value[:80] if isinstance(value, str) else value})
        if q.get('fallback') in ('true', '1'):
            qs = qs.filter(fallback_used=True)
        limit, offset = limit_offset(q, default=50, maximum=200)
        return Response({'from': first, 'to': last, 'count': qs.count(),
                         'results': [ops.trace(t) for t in qs.order_by('-created_at')[offset:offset + limit]]})


class TraceDetailView(_OpsView):
    @extend_schema(responses={200: OpenApiResponse(description='One turn')})
    def get(self, request, pk):
        from .models import AITrace
        t = AITrace.objects.filter(pk=pk).first()
        if t is None:
            raise exceptions.NotFound('Trace not found.')
        return Response(ops.trace(t))
