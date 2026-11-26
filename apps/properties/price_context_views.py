"""
Indicative price context API (T-4020, FR-VAL-001…004, ADR-066).

    GET /api/v1/price-context/?listing=<id|ref|slug>
    GET /api/v1/price-context/?country=PK&city=Lahore&currency=PKR&size_m2=126&category=residential
                               [&subtype=house][&asking_price=25000000]

Public (throttle ``price_context``). A listing must be public, or one the caller manages.
"""
from decimal import Decimal, InvalidOperation

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import price_context as PC
from .investment_views import _listing

_P = [OpenApiParameter(n, OpenApiTypes.STR, required=False) for n in
      ('listing', 'country', 'city', 'currency', 'size_m2', 'category', 'subtype', 'asking_price')]


def _number(q, name, required=False):
    raw = (q.get(name) or '').strip()
    if not raw:
        if required:
            raise exceptions.ValidationError({name: 'Required.'})
        return None
    try:
        d = Decimal(raw)
    except InvalidOperation:
        raise exceptions.ValidationError({name: 'A number.'}) from None
    if not d.is_finite() or d <= 0 or d > 10 ** 13:
        raise exceptions.ValidationError({name: 'A positive number.'})
    return d


class PriceContextView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'price_context'

    @extend_schema(operation_id='price_context', parameters=_P, responses={
        200: OpenApiResponse(description='Asking price per m² of comparable listings with N, spread, age, '
                                         'confidence and provenance; `available: false` below the minimum sample'),
        400: OpenApiResponse(description='Invalid query'), 404: OpenApiResponse(description='Listing not found')})
    def get(self, request):
        from apps.markets.services import get_market
        from .models import Property
        q = request.query_params
        if q.get('listing'):
            return Response(PC.for_listing(_listing(request, q['listing'])))
        country, city, currency = ((q.get(k) or '').strip() for k in ('country', 'city', 'currency'))
        if len(country) != 2 or not city or len(currency) != 3 or len(city) > 100:
            raise exceptions.ValidationError({'detail': 'Give a listing, or country (2 letters), city and '
                                                        'currency (3 letters).'})
        category, subtype = (q.get('category') or None), (q.get('subtype') or None)
        if category and category not in Property.Category.values:
            raise exceptions.ValidationError({'category': 'Unknown category.'})
        if subtype and subtype not in dict(Property.SUBTYPE_CHOICES):
            raise exceptions.ValidationError({'subtype': 'Unknown subtype.'})
        info = get_market(country.upper())
        return Response(PC.context(country=country, city=city, currency=currency,
                                   size_m2=_number(q, 'size_m2', required=True), category=category,
                                   subtype=subtype, asking_price=_number(q, 'asking_price'),
                                   market_name=getattr(info, 'name', '') or country.upper()))
