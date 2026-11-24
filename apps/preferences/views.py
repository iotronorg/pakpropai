"""``/me/``: the signed-in person's favourites, saved searches and preferences (T-2040)."""
from django.db.models import prefetch_related_objects
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_field
from rest_framework import exceptions, serializers, status
from rest_framework.generics import ListAPIView, get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.enquiries.views import ThreadMessageThrottle
from apps.properties import criteria as C
from apps.properties import public_listings as P
from apps.properties.public_views import PublicCardSerializer

from . import alerts as A
from . import services as S
from .models import Favourite, PreferenceItem, SavedSearch


class PreferenceRefused(exceptions.APIException):
    def __init__(self, exc: S.PreferenceError):
        self.status_code = 403 if exc.code == 'consent_required' else 409
        super().__init__(str(exc), f'preferences.{exc.code}')


def _criteria(call):
    """Run ``call``; criteria problems become a 400."""
    try:
        return call()
    except C.CriteriaInvalid as exc:
        raise exceptions.ValidationError(exc.errors) from exc
    except C.LocationAmbiguous as exc:
        raise exceptions.ValidationError({'location': [f'Several places are called "{exc.text}".'],
                                          'candidates': [c['label'] for c in exc.candidates]}) from exc
    except S.PreferenceError as exc:
        raise PreferenceRefused(exc) from exc


# ── Favourites ────────────────────────────────────────────────────────────────

class FavouriteSerializer(serializers.ModelSerializer):
    available = serializers.SerializerMethodField(help_text='False: no longer public')
    card = serializers.SerializerMethodField()

    class Meta:
        model = Favourite
        fields = ['listing', 'kind', 'note', 'available', 'card', 'created_at', 'updated_at']
        read_only_fields = fields

    def get_available(self, f) -> bool:
        return f.listing_id in self.context['cards']

    @extend_schema_field(PublicCardSerializer(allow_null=True))
    def get_card(self, f):
        return self.context['cards'].get(f.listing_id)


def _cards(request, ids) -> dict:
    found = S.visible(ids)
    prefetch_related_objects(list(found.values()), P.safe_images())
    return {pk: PublicCardSerializer(P.card(p, request)).data for pk, p in found.items()}


class KeepSerializer(serializers.Serializer):
    listing_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=Favourite.Kind.choices, default=Favourite.Kind.SAVED)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class FavouriteListView(ListAPIView):
    """Saved (default) or dismissed listings, latest first. POST saves or updates one."""
    permission_classes = [IsAuthenticated]
    serializer_class = FavouriteSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Favourite.objects.none()
        kind = self.request.query_params.get('kind', Favourite.Kind.SAVED)
        if kind not in Favourite.Kind.values:
            raise exceptions.ValidationError({'kind': ['saved or dismissed.']})
        return Favourite.objects.filter(user=self.request.user, kind=kind)

    @extend_schema(parameters=[OpenApiParameter('kind', str, enum=Favourite.Kind.values)])
    def get(self, request, *args, **kwargs):
        rows = self.paginate_queryset(self.get_queryset())
        cards = _cards(request, [f.listing_id for f in rows])
        return self.get_paginated_response(FavouriteSerializer(rows, many=True, context={'cards': cards}).data)

    @extend_schema(request=KeepSerializer, responses={200: FavouriteSerializer,
                                                      404: OpenApiResponse(description='Not public')})
    def post(self, request):
        s = KeepSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        listing = S.visible([d['listing_id']]).get(d['listing_id'])
        if listing is None:
            raise exceptions.NotFound('Listing not found.')
        fav = _criteria(lambda: S.keep(request.user, listing, kind=d['kind'], note=d.get('note')))
        return Response(FavouriteSerializer(fav, context={'cards': _cards(request, [listing.pk])}).data)


class FavouriteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, listing_id):
        if not S.drop(request.user, listing_id):
            raise exceptions.NotFound('Not in your favourites.')
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Saved searches ────────────────────────────────────────────────────────────

class SavedSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedSearch
        fields = ['id', 'name', 'criteria', 'frequency', 'channel', 'is_active', 'paused_reason', 'last_alert_at',
                  'created_at']
        read_only_fields = fields


class SavedSearchWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    criteria = serializers.JSONField(help_text='SearchCriteria, as POST /search/')
    frequency = serializers.ChoiceField(choices=SavedSearch.Frequency.choices, default=SavedSearch.Frequency.DAILY)
    channel = serializers.ChoiceField(choices=SavedSearch.Channel.choices, default=SavedSearch.Channel.IN_APP)
    alert_consent = serializers.BooleanField(help_text='The person agrees to be alerted on this channel')

    def validate_alert_consent(self, value):
        if value is not True:
            raise serializers.ValidationError('Consent to be alerted is required.')
        return value


class SavedSearchEditSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    criteria = serializers.JSONField()
    frequency = serializers.ChoiceField(choices=SavedSearch.Frequency.choices)
    channel = serializers.ChoiceField(choices=SavedSearch.Channel.choices)
    is_active = serializers.BooleanField()


class SavedSearchListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SavedSearchSerializer

    def get_throttles(self):
        return [ThreadMessageThrottle()] if self.request.method == 'POST' else super().get_throttles()

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return SavedSearch.objects.none()
        return SavedSearch.objects.filter(user=self.request.user)

    @extend_schema(request=SavedSearchWriteSerializer, responses={201: SavedSearchSerializer})
    def post(self, request):
        s = SavedSearchWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        d.pop('alert_consent')
        search = _criteria(lambda: A.create(request.user, d.pop('criteria'), **d))
        return Response(SavedSearchSerializer(search).data, status=status.HTTP_201_CREATED)


class SavedSearchDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def search(self, request, pk):
        return get_object_or_404(SavedSearch, pk=pk, user=request.user)

    @extend_schema(responses=SavedSearchSerializer)
    def get(self, request, pk):
        return Response(SavedSearchSerializer(self.search(request, pk)).data)

    @extend_schema(request=SavedSearchEditSerializer, responses=SavedSearchSerializer)
    def patch(self, request, pk):
        from django.utils import timezone
        search = self.search(request, pk)
        s = SavedSearchEditSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        active, fields = d.pop('is_active', None), {}
        now = timezone.now()
        if 'criteria' in d:
            fields.update(criteria=A.dump(_criteria(lambda: A.clean(d.pop('criteria')))), since=now)
        if 'frequency' in d:
            fields['next_run_at'] = A._next(d['frequency'], now)
        SavedSearch.objects.filter(pk=search.pk).update(**fields, **d, updated_at=now)
        search.refresh_from_db()
        if active is True and not search.is_active:
            A.resume(search)
        elif active is False:
            A.pause(search, 'user')
        return self.get(request, pk)

    @extend_schema(request=None, responses={204: None})
    def delete(self, request, pk):
        self.search(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StopThrottle(AnonRateThrottle):
    scope = 'public_convert'


class StopSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=300)


class StopAlertsView(APIView):
    """The stop link in an alert; works without signing in."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [StopThrottle]

    @extend_schema(request=StopSerializer, responses={204: None, 400: OpenApiResponse(description='Invalid link')})
    def post(self, request):
        s = StopSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not A.stop(s.validated_data['token']):
            raise exceptions.ValidationError({'token': ['Invalid link.']})
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Preference profile ────────────────────────────────────────────────────────

class PreferenceItemSerializer(serializers.ModelSerializer):
    value = serializers.JSONField(read_only=True)

    class Meta:
        model = PreferenceItem
        fields = ['id', 'market', 'key', 'value', 'source', 'confidence', 'updated_at']
        read_only_fields = fields


class ProfileSerializer(serializers.Serializer):
    consent = serializers.BooleanField()
    consent_text = serializers.CharField()
    items = PreferenceItemSerializer(many=True)


class ProfileWriteSerializer(serializers.Serializer):
    market = serializers.CharField(max_length=2)
    values = serializers.DictField(help_text=f'Criteria fields: {", ".join(S.KEYS)}')


class MemoryConsentSerializer(serializers.Serializer):
    consent = serializers.BooleanField()


class ProfileView(APIView):
    """GET: everything remembered. PATCH: state preferences. DELETE: forget all (the consent stays)."""
    permission_classes = [IsAuthenticated]

    def answer(self, user):
        return Response(ProfileSerializer({'consent': S.remembers(user), 'consent_text': S.MEMORY_TEXT,
                                           'items': PreferenceItem.objects.filter(user=user)}).data)

    @extend_schema(responses=ProfileSerializer)
    def get(self, request):
        return self.answer(request.user)

    @extend_schema(request=ProfileWriteSerializer,
                   responses={200: ProfileSerializer, 403: OpenApiResponse(description='No consent')})
    def patch(self, request):
        s = ProfileWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        _criteria(lambda: S.state(request.user, s.validated_data['market'], s.validated_data['values']))
        return self.answer(request.user)

    @extend_schema(request=None, responses={204: None})
    def delete(self, request):
        PreferenceItem.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileItemView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='me_preference_item_destroy', request=None, responses={204: None}, parameters=[
        OpenApiParameter('pk', OpenApiTypes.UUID, OpenApiParameter.PATH)])
    def delete(self, request, pk):
        get_object_or_404(PreferenceItem, pk=pk, user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MemoryConsentView(ProfileView):
    """Turn preference memory on or off. Off deletes everything remembered."""
    http_method_names = ['put', 'options']

    @extend_schema(request=MemoryConsentSerializer, responses=ProfileSerializer)
    def put(self, request):
        s = MemoryConsentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        S.set_memory(request.user, s.validated_data['consent'])
        return self.answer(request.user)
