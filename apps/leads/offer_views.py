"""Offers register API (T-4060, FR-CRM-009, ADR-068): /api/v1/leads/offers/."""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import exceptions, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.idempotency import IDEMPOTENCY_PARAMETER, idempotent

from . import offers as OF
from .models import Lead, LeadOffer


class OfferSerializer(serializers.ModelSerializer):
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all())
    property = serializers.UUIDField(source='property_id', required=False, allow_null=True)
    previous = serializers.PrimaryKeyRelatedField(queryset=LeadOffer.objects.all(), required=False, allow_null=True)
    amount = serializers.IntegerField(min_value=1, max_value=OF.MAX_AMOUNT)
    property_title = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = LeadOffer
        fields = ['id', 'lead', 'property', 'property_title', 'previous', 'party', 'amount', 'currency', 'basis',
                  'conditions', 'expires_at', 'status', 'decided_at', 'decision_note', 'created_by_name',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'decided_at', 'decision_note', 'created_at', 'updated_at']
        extra_kwargs = {'conditions': {'max_length': 1000}}

    def get_property_title(self, o) -> str | None:
        return o.property.title if o.property_id and o.property else None

    def get_created_by_name(self, o) -> str | None:
        return o.created_by.name if o.created_by_id and o.created_by else None


class OfferDecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=sorted(OF.DECISIONS))
    note = serializers.CharField(required=False, allow_blank=True, max_length=300)


def _refused(exc: OF.OfferError):
    if exc.field == 'lead':
        return exceptions.NotFound(str(exc))
    if exc.field == 'detail':
        return exceptions.PermissionDenied(str(exc))
    if exc.code in ('offer.not_open', 'offer.already_accepted'):
        e = exceptions.APIException({'detail': str(exc), 'code': exc.code})
        e.status_code = status.HTTP_409_CONFLICT
        return e
    return exceptions.ValidationError({exc.field: str(exc)})


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('lead', str), OpenApiParameter('property', str),
    OpenApiParameter('status', str, enum=list(LeadOffer.Status.values))]))
class LeadOfferViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                       viewsets.GenericViewSet):
    """Record offers and counter-offers; decide with ``POST …/<id>/decide/``. Offers are never edited."""
    permission_classes = [IsAuthenticated]
    serializer_class = OfferSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return LeadOffer.objects.none()
        qs, q = OF.visible(self.request.user), self.request.query_params
        for key, field in (('lead', 'lead_id'), ('property', 'property_id')):
            if q.get(key):
                qs = qs.filter(**{field: q[key]}) if _uuid(q[key]) else qs.none()
        if q.get('status') in LeadOffer.Status.values:
            qs = qs.filter(status=q['status'])
        return qs

    def list(self, request, *args, **kwargs):
        OF.expire_due(qs=self.get_queryset())
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        offer = self.get_object()
        OF.expire_due(qs=LeadOffer.objects.filter(pk=offer.pk))
        offer.refresh_from_db()
        return Response({**OfferSerializer(offer).data,
                         'negotiation': OfferSerializer(OF.chain(offer), many=True).data})

    @extend_schema(parameters=[IDEMPOTENCY_PARAMETER], responses={201: OfferSerializer,
                   409: OpenApiResponse(description='The offer answered is no longer open')})
    @idempotent
    def create(self, request, *args, **kwargs):
        s = OfferSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            offer = OF.create(request.user, d['lead'], amount=d['amount'], currency=d['currency'],
                              party=d.get('party', LeadOffer.Party.CLIENT), basis=d.get('basis', LeadOffer.Basis.TOTAL),
                              property_id=d.get('property_id'), conditions=d.get('conditions', ''),
                              expires_at=d.get('expires_at'), previous=d.get('previous'))
        except OF.OfferError as exc:
            raise _refused(exc) from None
        return Response(OfferSerializer(offer).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=OfferDecisionSerializer, responses={200: OfferSerializer,
                   409: OpenApiResponse(description='Not open, or another offer is already accepted')})
    @action(detail=True, methods=['post'])
    def decide(self, request, pk=None):
        offer = self.get_object()
        s = OfferDecisionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            offer = OF.decide(request.user, offer, s.validated_data['status'], s.validated_data.get('note', ''))
        except OF.OfferError as exc:
            raise _refused(exc) from None
        return Response(OfferSerializer(offer).data)

    @extend_schema(parameters=[OpenApiParameter('lead', str)],
                   responses={200: OpenApiResponse(description='Offer counts by status')})
    @action(detail=False, methods=['get'])
    def summary(self, request):
        return Response(OF.summary(self.get_queryset()))


def _uuid(value) -> bool:
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False
