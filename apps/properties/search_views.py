"""``POST /api/v1/search/`` (T-2030): public listing search over ``SearchCriteria``."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.throttles import PropertySearchThrottle

from . import criteria as C
from . import listing_search as S


class ListingSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    ref_no = serializers.CharField()
    title = serializers.CharField()
    city = serializers.CharField()
    location = serializers.CharField()
    price = serializers.IntegerField(allow_null=True)
    currency = serializers.CharField()
    price_type = serializers.CharField()
    purpose = serializers.CharField()
    category = serializers.CharField(allow_null=True)
    subtype = serializers.CharField(allow_null=True)
    size = serializers.CharField(allow_blank=True)
    bedrooms = serializers.IntegerField(allow_null=True)
    bathrooms = serializers.IntegerField(allow_null=True)
    verified = serializers.BooleanField()


class SearchHitSerializer(serializers.Serializer):
    listing = ListingSummarySerializer()
    match_score = serializers.IntegerField()
    reasons = serializers.ListField(child=serializers.CharField())
    tradeoffs = serializers.ListField(child=serializers.CharField())
    sponsored = serializers.BooleanField()


class SearchResponseSerializer(serializers.Serializer):
    results = SearchHitSerializer(many=True)
    total = serializers.IntegerField()
    facets = serializers.JSONField(allow_null=True)
    relaxations = serializers.JSONField()
    next_cursor = serializers.CharField(allow_null=True)
    criteria_echo = serializers.JSONField()
    assumed = serializers.ListField(child=serializers.CharField())
    ranking_version = serializers.CharField()


class SearchView(APIView):
    """
    Search visible listings. The body is ``SearchCriteria`` plus ``sort``,
    ``cursor``, ``limit`` (1–50) and ``facets``. 422 ``location.ambiguous`` lists
    candidates; send the chosen ``location.location_ids``.
    """
    permission_classes = [AllowAny]
    throttle_classes = [PropertySearchThrottle]      # anonymous callers are counted by IP

    @extend_schema(request=OpenApiTypes.OBJECT, responses={
        200: SearchResponseSerializer, 400: OpenApiResponse(description='Field errors'),
        422: OpenApiResponse(description='Ambiguous location')})
    def post(self, request):
        if not isinstance(request.data, dict):
            raise exceptions.ValidationError({'criteria': ['Must be an object.']})
        body = {k: request.data[k] for k in request.data}
        sort, cursor = body.pop('sort', 'relevance'), body.pop('cursor', '') or ''
        limit, facets = body.pop('limit', 20), body.pop('facets', False)
        errors = {}
        if sort not in S.SORTS:
            errors['sort'] = [f'One of {", ".join(S.SORTS)}.']
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= S.MAX_LIMIT:
            errors['limit'] = [f'1 to {S.MAX_LIMIT}.']
        if not isinstance(cursor, str):
            errors['cursor'] = ['Must be a string.']
        if not isinstance(facets, bool):
            errors['facets'] = ['Must be true or false.']
        if errors:
            raise exceptions.ValidationError(errors)
        try:
            crit, assumed = C.prepare(C.parse(body))
            found = S.execute(crit, sort=sort, limit=limit, cursor=cursor, with_facets=facets)
        except C.CriteriaInvalid as exc:
            raise exceptions.ValidationError(exc.errors) from exc
        except S.CursorInvalid as exc:
            raise exceptions.ValidationError({'cursor': [str(exc)]}) from exc
        except C.LocationAmbiguous as exc:
            return Response({'detail': f'Several places are called "{exc.text}".', 'code': 'location.ambiguous',
                             'candidates': exc.candidates}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return Response(SearchResponseSerializer({
            'results': [{'listing': S.summary(h.listing), 'match_score': h.score, 'reasons': h.reasons,
                         'tradeoffs': h.tradeoffs, 'sponsored': False} for h in found.hits],
            'total': found.total, 'facets': found.facets, 'relaxations': found.relaxations,
            'next_cursor': found.next_cursor, 'criteria_echo': crit.model_dump(mode='json', exclude_defaults=True),
            'assumed': assumed, 'ranking_version': found.ranking_version}).data)
