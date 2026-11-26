"""``compare/`` for everyone (T-2041). The older ``properties/compare/`` stays for agents."""
import uuid

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.core.permissions import FeatureEnabled, get_user_org
from apps.core.throttles import PropertySearchThrottle

from . import comparison as K
from . import criteria as C
from . import public_listings as P
from .public_views import PARAMS, PublicCardSerializer, PublicView, _bool, criteria_from_query


class ComparedMoneySerializer(serializers.Serializer):
    amount = serializers.IntegerField()
    currency = serializers.CharField()
    basis = serializers.CharField(allow_null=True, help_text='total, month or unspecified')
    converted = serializers.IntegerField(allow_null=True, help_text='Approximate, in the answer\'s currency')


class ComparedListingSerializer(serializers.Serializer):
    listing = PublicCardSerializer()
    price_on_request = serializers.BooleanField()
    price = ComparedMoneySerializer(allow_null=True)
    price_per_m2 = ComparedMoneySerializer(allow_null=True)
    deposit = serializers.IntegerField(allow_null=True)
    service_charge = serializers.IntegerField(allow_null=True)
    size_m2 = serializers.FloatField(allow_null=True)
    floor = serializers.IntegerField(allow_null=True)
    year_built = serializers.IntegerField(allow_null=True)
    parking_spaces = serializers.IntegerField(allow_null=True)
    furnishing = serializers.CharField(allow_null=True)
    completion = serializers.CharField(allow_null=True)
    pets_allowed = serializers.BooleanField(allow_null=True)
    installments = serializers.BooleanField(allow_null=True)
    verification = serializers.CharField()
    status = serializers.CharField()
    listed_days = serializers.IntegerField()
    amenities_only_here = serializers.ListField(child=serializers.CharField())
    match = serializers.DictField(child=serializers.CharField(), allow_null=True,
                                  help_text='Condition → yes, no or unknown')
    running_costs = serializers.JSONField(allow_null=True, help_text='Estimated purchase fees and yearly costs '
                                          '(sale listings in markets with a calculator; T-4030)')


class FxBasisSerializer(serializers.Serializer):
    approximate = serializers.BooleanField()
    as_of = serializers.DateField()
    source = serializers.CharField()


class ComparisonSerializer(serializers.Serializer):
    currency = serializers.CharField()
    fx = FxBasisSerializer(allow_null=True)
    items = ComparedListingSerializer(many=True)
    shared_amenities = serializers.ListField(child=serializers.CharField())
    best = serializers.DictField(child=serializers.CharField(allow_null=True))
    unavailable = serializers.ListField(child=serializers.UUIDField())
    criteria_echo = serializers.JSONField(allow_null=True)


def _ids(query) -> list[uuid.UUID]:
    raw = [x.strip() for x in query.get('ids', '').split(',') if x.strip()]
    try:
        ids = list(dict.fromkeys(uuid.UUID(x) for x in raw))
    except ValueError:
        raise exceptions.ValidationError({'ids': ['Must be listing ids.']}) from None
    if not K.MIN <= len(ids) <= K.MAX:
        raise exceptions.ValidationError({'ids': [f'Give {K.MIN} to {K.MAX} different listings.']})
    return ids


def _currency(query) -> str:
    value = query.get('display_currency', '').strip().upper()
    if value and not (len(value) == 3 and value.isascii() and value.isalpha()):
        raise exceptions.ValidationError({'display_currency': ['ISO code, e.g. USD.']})
    return value


def _answer(request, data: dict | None, *, viewer_org=None) -> dict:
    ids, currency = _ids(request.query_params), _currency(request.query_params)
    crit = None
    if data:
        try:
            crit, _ = C.prepare(C.parse(data))
        except C.CriteriaInvalid as exc:
            raise exceptions.ValidationError(exc.errors) from exc
    listings = K.load(ids, viewer_org)
    if len(listings) < K.MIN:
        raise exceptions.NotFound('Fewer than two of these listings are available.')
    out = K.compare(listings, currency=currency, criteria=crit, viewer_org=viewer_org)
    cards = {str(p.pk): P.card(p, request) for p in listings}
    for item in out['items']:
        item['listing'] = cards[item['id']]
    have = {p.pk for p in listings}
    return ComparisonSerializer({
        **out, 'unavailable': [i for i in ids if i not in have],
        'criteria_echo': crit.model_dump(mode='json', exclude_defaults=True) if crit else None}).data


def _ambiguous(exc):
    return Response({'detail': f'Several places are called "{exc.text}".', 'code': 'location.ambiguous',
                     'candidates': exc.candidates}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


_QUERY = [OpenApiParameter('ids', str, required=True, description='2–4 listing ids, comma-separated'),
          OpenApiParameter('display_currency', str, description='Convert prices approximately'),
          *(OpenApiParameter(n, str) for n in PARAMS)]
_ANSWERS = {200: ComparisonSerializer, 400: OpenApiResponse(description='Field errors'),
            404: OpenApiResponse(description='Fewer than two listings available'),
            422: OpenApiResponse(description='Ambiguous location')}


class PublicCompareView(PublicView):
    """Compare public listings. Search parameters, when given, are answered per listing in ``match``."""

    @extend_schema(parameters=_QUERY, responses=_ANSWERS)
    def get(self, request):
        try:
            return self.cached(request, _answer(request, criteria_from_query(request.query_params)))
        except C.LocationAmbiguous as exc:
            return _ambiguous(exc)


class CompareView(APIView):
    """
    The same for a signed-in person: professionals also see their organization's
    sandbox listings, and ``use_preferences=true`` adds the person's own stated
    preferences (only with their consent) below the parameters given.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [PropertySearchThrottle]

    @extend_schema(parameters=[*_QUERY, OpenApiParameter('use_preferences', bool)], responses=_ANSWERS)
    def get(self, request):
        data = criteria_from_query(request.query_params)
        try:
            use = _bool(request.query_params.get('use_preferences', 'false'))
        except ValueError as exc:
            raise exceptions.ValidationError({'use_preferences': [str(exc)]}) from exc
        viewer_org = get_user_org(request.user) if request.user.role in ('agent', 'developer') else None
        if use:
            from apps.preferences.services import stated
            market = (data.get('market') or '').upper()
            if not market:
                first = next(iter(K.load(_ids(request.query_params), viewer_org)), None)
                market = (first.market_id or first.country) if first else ''
            if mine := stated(request.user, market):
                data = {**mine, **data, 'market': market}
        try:
            answer = _answer(request, data, viewer_org=viewer_org)
        except C.LocationAmbiguous as exc:
            return _ambiguous(exc)
        return Response(answer, headers={'Cache-Control': 'private, no-store'})


class NarrativeThrottle(UserRateThrottle):
    scope = 'comparison_narrative'           # each call is a paid model call


class CompareNarrativeView(APIView):
    """
    ``GET /compare/narrative/?ids=…`` (T-4030, FR-CMP-003): the comparison plus a short
    written comparison built only from its cells. Signed in; flag ``feature_comparison_narrative``.
    """
    permission_classes = [IsAuthenticated, FeatureEnabled('feature_comparison_narrative')]
    throttle_classes = [NarrativeThrottle]

    @extend_schema(operation_id='compare_narrative', parameters=_QUERY[:2], responses={
        200: OpenApiResponse(description='Comparison plus `narrative` (cited sentences, unknowns, left out)'),
        403: OpenApiResponse(description='Feature off'), 404: OpenApiResponse(description='Fewer than two listings'),
        503: OpenApiResponse(description='No model available')})
    def get(self, request):
        from apps.ai.comparison_narrative import narrative
        from apps.ai.llm import AIStopped, LLMUnavailable
        viewer_org = get_user_org(request.user) if request.user.role in ('agent', 'developer') else None
        ids, currency = _ids(request.query_params), _currency(request.query_params)
        listings = K.load(ids, viewer_org)
        if len(listings) < K.MIN:
            raise exceptions.NotFound('Fewer than two of these listings are available.')
        out = K.compare(listings, currency=currency, viewer_org=viewer_org)
        headers = {'Cache-Control': 'private, no-store'}
        try:
            text = narrative(out, country=listings[0].country, user=request.user)
        except LLMUnavailable as exc:
            stopped = isinstance(exc, AIStopped)
            return Response({**out, 'narrative': None, 'code': 'ai.stopped' if stopped else 'ai.unavailable'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE, headers=headers)
        return Response({**out, 'narrative': text}, headers=headers)
