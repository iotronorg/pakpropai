"""
Public, cacheable listing reads (T-2031, GAP-002): no account, no cookies, the
same answer for everyone. The search shares criteria and engine with ``/search/``.
"""
import hashlib

from django.db.models import prefetch_related_objects
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from . import analytics as A
from . import criteria as C
from . import listing_search as S
from . import public_listings as P
from .search_views import ListingCopySerializer, ListingSummarySerializer

MAX_AGE = 60


class PublicListingThrottle(AnonRateThrottle):
    scope = 'public_listings'


class PublicImageSerializer(serializers.Serializer):
    url = serializers.CharField()
    thumbnail_url = serializers.CharField()
    alt_text = serializers.CharField(allow_blank=True)
    width = serializers.IntegerField(allow_null=True)
    height = serializers.IntegerField(allow_null=True)


class PublicCardSerializer(ListingSummarySerializer):
    slug = serializers.CharField()
    image = PublicImageSerializer(allow_null=True)


class PublicAmenitySerializer(serializers.Serializer):
    key = serializers.CharField()
    labels = serializers.JSONField()


class ListerSerializer(serializers.Serializer):
    type = serializers.CharField()
    name = serializers.CharField(allow_null=True)
    slug = serializers.CharField(allow_null=True)


class PublicListingSerializer(PublicCardSerializer):
    description = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    country = serializers.CharField()
    size_m2 = serializers.FloatField(allow_null=True)
    plot_size_m2 = serializers.FloatField(allow_null=True)
    floor = serializers.IntegerField(allow_null=True)
    total_floors = serializers.IntegerField(allow_null=True)
    year_built = serializers.IntegerField(allow_null=True)
    parking_spaces = serializers.IntegerField(allow_null=True)
    pets_allowed = serializers.BooleanField(allow_null=True)
    furnishing = serializers.CharField(allow_null=True)
    completion = serializers.CharField(allow_null=True)
    installments = serializers.BooleanField()
    price_on_request = serializers.BooleanField()
    price_negotiable = serializers.BooleanField()
    deposit = serializers.IntegerField(allow_null=True)
    service_charge = serializers.IntegerField(allow_null=True)
    amenities = PublicAmenitySerializer(many=True)
    attributes = serializers.JSONField()
    images = PublicImageSerializer(many=True)
    latitude = serializers.FloatField(allow_null=True)
    longitude = serializers.FloatField(allow_null=True)
    location_precision = serializers.CharField()
    location_path = serializers.CharField(allow_null=True)
    lister = ListerSerializer()
    published_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class PublicHitSerializer(serializers.Serializer):
    listing = PublicCardSerializer()
    match_score = serializers.IntegerField()
    reasons = serializers.ListField(child=serializers.CharField())
    tradeoffs = serializers.ListField(child=serializers.CharField())
    also_listed = ListingCopySerializer(many=True)
    also_listed_count = serializers.IntegerField()


class PublicSearchSerializer(serializers.Serializer):
    results = PublicHitSerializer(many=True)
    total = serializers.IntegerField()
    next_cursor = serializers.CharField(allow_null=True)
    ranking_version = serializers.CharField()


class PublicView(APIView):
    """Credentials are ignored, so a shared cache can never hold a personal answer."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicListingThrottle]
    renderer_classes = [JSONRenderer]

    def cached(self, request, data):
        body = JSONRenderer().render(data)
        etag = f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'
        sent = {t.strip() for t in request.headers.get('If-None-Match', '').split(',')}
        response = (Response(status=status.HTTP_304_NOT_MODIFIED) if etag in sent or '*' in sent else Response(data))
        response['ETag'] = etag
        response['Cache-Control'] = f'public, max-age={MAX_AGE}'
        return response


def _int(v):
    if not v.isascii() or not v.isdigit() or len(v) > 15:
        raise ValueError('Must be a whole number.')
    return int(v)


def _float(v):
    try:
        return float(v)
    except ValueError:
        raise ValueError('Must be a number.') from None


def _bool(v):
    if v.lower() not in ('true', 'false', '1', '0'):
        raise ValueError('Must be true or false.')
    return v.lower() in ('true', '1')


def _csv(v):
    return [x.strip() for x in v.split(',') if x.strip()]


# query parameter → (criteria path, converter)
PARAMS = {
    'market': ('market', str), 'purpose': ('purpose', str), 'category': ('category', str),
    'subtypes': ('subtypes', _csv), 'location': ('location.free_text', str),
    'location_ids': ('location.location_ids', lambda v: [_int(x) for x in _csv(v)]),
    'min_price': ('price.min', _int), 'max_price': ('price.max', _int), 'currency': ('price.currency', str),
    'price_period': ('price.period', str), 'min_bedrooms': ('bedrooms.min', _int),
    'max_bedrooms': ('bedrooms.max', _int), 'min_bathrooms': ('bathrooms.min', _int),
    'min_size': ('size.min', _float), 'max_size': ('size.max', _float), 'size_unit': ('size.unit', str),
    'amenities': ('amenities_required', _csv), 'accessibility': ('accessibility', _csv),
    'furnishing': ('furnishing', str), 'completion': ('completion', str), 'pets_allowed': ('pets_allowed', _bool),
    'installments': ('installments', _bool), 'verified_only': ('verified_only', _bool),
    'listed_within_days': ('listed_within_days', _int), 'q': ('keywords', str),
}


def criteria_from_query(query) -> dict:
    data, errors = {}, {}
    for name, (path, convert) in PARAMS.items():
        raw = query.get(name, '').strip()
        if not raw:
            continue
        try:
            value = convert(raw)
        except ValueError as exc:
            errors[name] = [str(exc)]
            continue
        target, *rest = path.split('.')
        if rest:
            data.setdefault(target, {})[rest[0]] = value
        else:
            data[target] = value
    if errors:
        raise exceptions.ValidationError(errors)
    return data


class PublicListingSearchView(PublicView):
    """Search public listings by query string. Unknown parameters (e.g. utm_*) are ignored."""

    @extend_schema(
        parameters=[OpenApiParameter(n, str) for n in (*PARAMS, 'cursor')]
        + [OpenApiParameter('sort', str, enum=S.SORTS), OpenApiParameter('limit', int),
           OpenApiParameter('collapse_duplicates', bool)],
        responses={200: PublicSearchSerializer, 400: OpenApiResponse(description='Field errors'),
                   422: OpenApiResponse(description='Ambiguous location')})
    def get(self, request):
        q = request.query_params
        sort = q.get('sort') or 'relevance'
        if sort not in S.SORTS:
            raise exceptions.ValidationError({'sort': [f'One of {", ".join(S.SORTS)}.']})
        try:
            limit = _int(q.get('limit') or '20')
            if not 1 <= limit <= S.MAX_LIMIT:
                raise ValueError
        except ValueError:
            raise exceptions.ValidationError({'limit': [f'1 to {S.MAX_LIMIT}.']}) from None
        collapse = None
        if q.get('collapse_duplicates', '').strip():
            try:
                collapse = _bool(q['collapse_duplicates'].strip())
            except ValueError as exc:
                raise exceptions.ValidationError({'collapse_duplicates': [str(exc)]}) from None
        try:
            crit, _ = C.prepare(C.parse(criteria_from_query(q)))
            found = S.execute(crit, sort=sort, limit=limit, cursor=q.get('cursor', ''), with_relaxations=False,
                              collapse_duplicates=collapse)
        except C.CriteriaInvalid as exc:
            raise exceptions.ValidationError(exc.errors) from exc
        except S.CursorInvalid as exc:
            raise exceptions.ValidationError({'cursor': [str(exc)]}) from exc
        except C.LocationAmbiguous as exc:
            return Response({'detail': f'Several places are called "{exc.text}".', 'code': 'location.ambiguous',
                             'candidates': exc.candidates}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        prefetch_related_objects([h.listing for h in found.hits], P.safe_images())
        A.shown(request, [h.listing for h in found.hits])
        return self.cached(request, PublicSearchSerializer({
            'results': [{'listing': P.card(h.listing, request), 'match_score': h.score, 'reasons': h.reasons,
                         'tradeoffs': h.tradeoffs, 'also_listed': h.also_listed,
                         'also_listed_count': h.also_listed_count} for h in found.hits],
            'total': found.total, 'next_cursor': found.next_cursor,
            'ranking_version': found.ranking_version}).data)


class PublicListingDetailView(PublicView):
    """One public listing by id, reference number or slug. ``slug`` in the answer is the canonical one."""

    @extend_schema(responses={200: PublicListingSerializer, 404: OpenApiResponse(description='Not public')})
    def get(self, request, slug_or_id):
        listing = P.lookup(slug_or_id)
        if listing is None:
            raise exceptions.NotFound('Listing not found.')
        A.detail_viewed(request, listing)
        return self.cached(request, PublicListingSerializer(P.detail(listing, request)).data)
