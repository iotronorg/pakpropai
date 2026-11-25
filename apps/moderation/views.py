"""Moderation queue for platform staff; reports and appeals for signed-in people (T-2014)."""
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.generics import ListAPIView, get_object_or_404
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.enquiries.models import ThreadReport
from apps.enquiries.views import ThreadMessageThrottle
from apps.properties.models import Property
from apps.properties.public_listings import public_q

from . import services as M
from .models import ModerationItem, UserReport


class IsModerator(BasePermission):
    def has_permission(self, request, view):
        return M.is_moderator(request.user)


class ModerationRefused(exceptions.APIException):
    def __init__(self, exc: M.ModerationError):
        self.status_code = 403 if exc.code in ('own_listing', 'forbidden') else 429 if exc.code == 'limit' else 409
        super().__init__(str(exc), f'moderation.{exc.code}')


def _call(fn):
    try:
        return fn()
    except M.ModerationError as exc:
        raise ModerationRefused(exc) from exc


class ReportSummarySerializer(serializers.ModelSerializer):
    """Reporters are not named in the queue."""
    class Meta:
        model = UserReport
        fields = ['id', 'category', 'text', 'created_at']
        read_only_fields = fields


class ModerationItemSerializer(serializers.ModelSerializer):
    ref_no = serializers.CharField(source='listing.ref_no', read_only=True)
    title = serializers.CharField(source='listing.title', read_only=True)
    market = serializers.CharField(source='listing.market_id', read_only=True, allow_null=True)
    listing_status = serializers.CharField(source='listing.status', read_only=True)
    owner_type = serializers.CharField(source='listing.listing_owner_type', read_only=True)
    overdue = serializers.SerializerMethodField()
    report_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ModerationItem
        fields = ['id', 'listing', 'ref_no', 'title', 'market', 'listing_status', 'owner_type', 'reason', 'status',
                  'flags', 'priority', 'due_at', 'overdue', 'report_count', 'reason_code', 'note', 'decided_at',
                  'created_at']
        read_only_fields = fields

    def get_overdue(self, i) -> bool:
        return i.status == ModerationItem.Status.OPEN and i.due_at < timezone.now()


class ModerationDetailSerializer(ModerationItemSerializer):
    description = serializers.CharField(source='listing.description', read_only=True)
    price = serializers.IntegerField(source='listing.price', read_only=True, allow_null=True)
    currency = serializers.CharField(source='listing.currency', read_only=True)
    digest = serializers.SerializerMethodField(help_text='Send it back as "seen" with the decision')
    reports = ReportSummarySerializer(many=True, read_only=True)

    class Meta(ModerationItemSerializer.Meta):
        fields = [*ModerationItemSerializer.Meta.fields, 'description', 'price', 'currency', 'appeal_text', 'digest',
                  'reports']
        read_only_fields = fields

    def get_digest(self, i) -> str:
        return M.digest(i.listing)


def _items():
    return ModerationItem.objects.select_related('listing').annotate(
        report_count=Count('reports')).order_by('-priority', 'due_at', 'pk')      # an aggregate drops Meta.ordering


class QueueSummaryView(APIView):
    """Open items per reason, and how many are overdue."""
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        qs = ModerationItem.objects.filter(status=ModerationItem.Status.OPEN)
        return Response({
            **{r: 0 for r in ModerationItem.Reason.values},
            **dict(qs.order_by().values_list('reason').annotate(n=Count('pk'))),
            'overdue': qs.filter(due_at__lt=timezone.now()).count(),
            'threads': ThreadReport.objects.filter(status=ThreadReport.Status.OPEN).count()})


class ItemListView(ListAPIView):
    """Highest priority first, then the nearest deadline."""
    permission_classes = [IsAuthenticated, IsModerator]
    serializer_class = ModerationItemSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return ModerationItem.objects.none()
        q, filters = self.request.query_params, Q()
        for name, values, default in (('status', ModerationItem.Status.values, 'open'),
                                      ('reason', ModerationItem.Reason.values, '')):
            value = q.get(name, default)
            if value and value not in values:
                raise exceptions.ValidationError({name: [f'One of {", ".join(values)}.']})
            if value:
                filters &= Q(**{name: value})
        if market := q.get('market', ''):
            filters &= Q(listing__market_id=market.upper()[:2])
        return _items().filter(filters)

    @extend_schema(parameters=[OpenApiParameter(n, str) for n in ('status', 'reason', 'market')])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class ItemDetailView(APIView):
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(responses=ModerationDetailSerializer)
    def get(self, request, pk):
        return Response(ModerationDetailSerializer(get_object_or_404(_items(), pk=pk)).data)


class DecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=['approve', 'reject', 'suspend', 'dismiss'])
    seen = serializers.CharField(max_length=64)
    reason_code = serializers.ChoiceField(choices=ModerationItem.ReasonCode.choices, required=False, allow_blank=True,
                                          default='')
    note = serializers.CharField(max_length=400, required=False, allow_blank=True, default='')

    def validate(self, d):
        if d['decision'] in ('reject', 'suspend') and not d['reason_code']:
            raise serializers.ValidationError({'reason_code': ['Required.']})
        return d


class DecisionView(APIView):
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(request=DecisionSerializer, responses={
        200: ModerationDetailSerializer, 403: OpenApiResponse(description='Own listing'),
        409: OpenApiResponse(description='Already decided, or the listing changed')})
    def post(self, request, pk):
        get_object_or_404(ModerationItem, pk=pk)
        s = DecisionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        _call(lambda: M.decide(pk, request.user, **s.validated_data))
        return Response(ModerationDetailSerializer(_items().get(pk=pk)).data)


class ReportSerializer(serializers.Serializer):
    listing_id = serializers.UUIDField()
    category = serializers.ChoiceField(choices=UserReport.Category.choices)
    text = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')


class ReportListingView(APIView):
    """Report a public listing. The lister does not learn who reported it."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ThreadMessageThrottle]

    @extend_schema(request=ReportSerializer, responses={
        202: None, 403: OpenApiResponse(description='Own listing'), 404: OpenApiResponse(description='Not public')})
    def post(self, request):
        s = ReportSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        p = Property.objects.visible_to(None).filter(public_q(), pk=d['listing_id']).first()
        if p is None:
            raise exceptions.NotFound('Listing not found.')
        _call(lambda: M.report(request.user, p, d['category'], d['text']))
        return Response(status=status.HTTP_202_ACCEPTED)


class AppealSerializer(serializers.Serializer):
    listing_id = serializers.UUIDField()
    text = serializers.CharField(max_length=1000)


class AppealView(APIView):
    """The lister appeals a suspension, once."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ThreadMessageThrottle]

    @extend_schema(request=AppealSerializer, responses={
        202: None, 404: OpenApiResponse(description='Not your listing'),
        409: OpenApiResponse(description='Not suspended, or already appealed')})
    def post(self, request):
        from apps.properties.lifecycle import can_manage
        s = AppealSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        p = Property.objects.filter(pk=s.validated_data['listing_id']).first()
        if p is None or not can_manage(request.user, p):
            raise exceptions.NotFound('Listing not found.')
        _call(lambda: M.appeal(request.user, p, s.validated_data['text']))
        return Response(status=status.HTTP_202_ACCEPTED)
