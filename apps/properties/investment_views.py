"""
Investment calculator API (T-4010, FR-INV-001…004, ADR-065).

    POST /api/v1/investment/calculate/    figures with formulas and sourced assumptions   (anyone)
    POST /api/v1/investment/commentary/   the same plus labelled AI commentary
                                          (signed in; flag ``feature_investment_commentary``)

Body (all optional except a price or a listing): ``listing`` (id, reference or
slug of a public listing, or one you manage), ``price``, ``currency``, ``country``,
``monthly_rent``, ``vacancy_pct``, ``maintenance_pct``, ``management_pct``,
``other_annual_costs``, ``purchase_costs``, ``annual_property_tax``,
``down_payment_pct``, ``interest_rate_pct``, ``tenure_years``, ``holding_years``,
``appreciation_pct`` (up to 3 yearly rates, the user's own scenarios).
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from apps.core.permissions import FeatureEnabled

from . import investment as I

FLAG = 'feature_investment_commentary'


class CommentaryThrottle(UserRateThrottle):
    scope = 'investment_commentary'          # each call is a paid model call


def _listing(request, ref):
    from .lifecycle import can_manage
    from .models import Property
    from .public_listings import lookup
    p = lookup(str(ref))
    if p is None and request.user.is_authenticated:
        import uuid
        try:
            own = Property.objects.filter(pk=uuid.UUID(str(ref))).first()
        except ValueError:
            own = None
        p = own if own is not None and can_manage(request.user, own) else None
    if p is None:
        raise exceptions.NotFound('Listing not found.')
    return p


def _calculate(request) -> dict:
    data = request.data if isinstance(request.data, dict) else None
    if data is None:
        raise exceptions.ValidationError({'detail': 'Send a JSON object.'})
    listing = _listing(request, data['listing']) if data.get('listing') else None
    try:
        return I.calculate(data, listing=listing)
    except I.InvestmentError as exc:
        raise exceptions.ValidationError({exc.field: str(exc)}) from None


class CalculateView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'investment'

    @extend_schema(operation_id='investment_calculate', request=OpenApiTypes.OBJECT,
                   responses={200: OpenApiResponse(description='Figures with formulas; assumptions with sources'),
                              400: OpenApiResponse(description='Invalid or missing input'),
                              404: OpenApiResponse(description='Listing not found')})
    def post(self, request):
        return Response(_calculate(request))


class CommentaryView(APIView):
    permission_classes = [IsAuthenticated, FeatureEnabled(FLAG)]
    throttle_classes = [CommentaryThrottle]

    @extend_schema(operation_id='investment_commentary', request=OpenApiTypes.OBJECT,
                   responses={200: OpenApiResponse(description='The calculation plus labelled commentary'),
                              403: OpenApiResponse(description='Feature off'),
                              503: OpenApiResponse(description='No model available')})
    def post(self, request):
        from apps.ai.investment_commentary import commentary
        from apps.ai.llm import AIStopped, LLMUnavailable
        result = _calculate(request)
        try:
            text = commentary(result, user=request.user)
        except LLMUnavailable as exc:
            stopped = isinstance(exc, AIStopped)
            return Response({**result, 'commentary': None,
                             'detail': 'The assistant is paused.' if stopped else 'Commentary is unavailable; try later.',
                             'code': 'ai.stopped' if stopped else 'ai.unavailable'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({**result, 'commentary': text})
