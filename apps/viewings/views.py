"""Availability (agents), slots and viewings (visitors). T-2060."""
from datetime import datetime, timedelta
from zoneinfo import available_timezones

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.idempotency import IDEMPOTENCY_PARAMETER, idempotent
from apps.enquiries import services as E
from apps.enquiries.views import EnquiryRefused, ThreadMessageThrottle
from apps.leads.models import Appointment

from . import services as V
from .models import AvailabilityException, AvailabilityRule, ViewingFeedback

MAX_RULES, MAX_EXCEPTIONS = 50, 200


class ViewingRefused(exceptions.APIException):
    def __init__(self, exc: V.ViewingError):
        self.status_code = {'slot_unknown': 400, 'limit': 429}.get(exc.code, 409)
        super().__init__(str(exc), f'viewing.{exc.code}')


class IsAgentUser(BasePermission):
    def has_permission(self, request, view):
        agent = getattr(request.user, 'agent_profile', None) if request.user.is_authenticated else None
        return agent is not None and agent.is_active


# ── Availability ──────────────────────────────────────────────────────────────

def _zone(value):
    if value not in available_timezones():
        raise serializers.ValidationError('Unknown IANA time zone.')
    return value


class RuleSerializer(serializers.ModelSerializer):
    weekday = serializers.IntegerField(min_value=0, max_value=6, help_text='0 = Monday')
    timezone = serializers.CharField(validators=[_zone], required=False, help_text='Default: your market')
    slot_minutes = serializers.IntegerField(min_value=15, max_value=240, default=60)

    class Meta:
        model = AvailabilityRule
        fields = ['weekday', 'start', 'end', 'timezone', 'slot_minutes']

    def validate(self, d):
        if d['end'] <= d['start']:
            raise serializers.ValidationError({'end': ['Must be after start.']})
        return d


class ExceptionSerializer(serializers.ModelSerializer):
    timezone = serializers.CharField(validators=[_zone], required=False)

    class Meta:
        model = AvailabilityException
        fields = ['date', 'available', 'start', 'end', 'timezone']

    def validate(self, d):
        start, end = d.get('start'), d.get('end')
        if (start is None) != (end is None) or (start is not None and end <= start):
            raise serializers.ValidationError('Give both times, end after start, or neither.')
        if d.get('available') and start is None:
            raise serializers.ValidationError('Extra hours need a start and an end.')
        return d


class AvailabilitySerializer(serializers.Serializer):
    rules = RuleSerializer(many=True)
    exceptions = ExceptionSerializer(many=True, required=False, default=list)

    def validate(self, d):
        if len(d['rules']) > MAX_RULES or len(d['exceptions']) > MAX_EXCEPTIONS:
            raise serializers.ValidationError(f'At most {MAX_RULES} rules and {MAX_EXCEPTIONS} exceptions.')
        by_day = {}
        for r in d['rules']:
            by_day.setdefault(r['weekday'], []).append((r['start'], r['end']))
        for spans in by_day.values():
            spans.sort()
            if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
                raise serializers.ValidationError({'rules': ['Hours of one weekday overlap.']})
        return d


class AvailabilityView(APIView):
    """The agent's own hours. PUT replaces them all; booked viewings are not affected."""
    permission_classes = [IsAuthenticated, IsAgentUser]

    def _answer(self, agent):
        return Response(AvailabilitySerializer({
            'rules': agent.availability_rules.all(),
            'exceptions': agent.availability_exceptions.filter(date__gte=timezone.now().date() - timedelta(days=1)),
        }).data)

    @extend_schema(responses=AvailabilitySerializer)
    def get(self, request):
        return self._answer(request.user.agent_profile)

    @extend_schema(request=AvailabilitySerializer, responses=AvailabilitySerializer)
    def put(self, request):
        from apps.markets.services import user_timezone
        s = AvailabilitySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        agent, tz = request.user.agent_profile, user_timezone(request.user).key
        with transaction.atomic():
            V.lock_agent(agent.pk)
            agent.availability_rules.all().delete()
            agent.availability_exceptions.all().delete()
            AvailabilityRule.objects.bulk_create(
                AvailabilityRule(agent=agent, **{'timezone': tz, **r}) for r in s.validated_data['rules'])
            AvailabilityException.objects.bulk_create(
                AvailabilityException(agent=agent, **{'timezone': tz, **x}) for x in s.validated_data['exceptions'])
        return self._answer(agent)


# ── Slots ─────────────────────────────────────────────────────────────────────

class SlotSerializer(serializers.Serializer):
    id = serializers.CharField()
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    local_start = serializers.CharField(help_text='Wall-clock time at the property')


class SlotListSerializer(serializers.Serializer):
    timezone = serializers.CharField()
    slots = SlotSerializer(many=True)


def _listing(pk):
    listing = E.visible_listing(pk)
    if listing is None:
        raise exceptions.NotFound('Listing not found.')
    return listing


class SlotListView(APIView):
    """Free viewing slots of a listing. An empty list: ask the lister in an enquiry."""
    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=[OpenApiParameter('listing', str, required=True),
                               OpenApiParameter('from', str, description='YYYY-MM-DD, default today'),
                               OpenApiParameter('days', int, description=f'1-{V.MAX_WINDOW_DAYS}, default 7')],
                   responses=SlotListSerializer)
    def get(self, request):
        q = request.query_params
        try:
            pk = serializers.UUIDField().to_internal_value(q.get('listing', ''))
            first = serializers.DateField().to_internal_value(q['from']) if q.get('from') else None
            days = serializers.IntegerField(min_value=1, max_value=V.MAX_WINDOW_DAYS).run_validation(q.get('days') or 7)
        except serializers.ValidationError as exc:
            raise exceptions.ValidationError({'query': exc.detail}) from exc
        listing = _listing(pk)
        tz = V.listing_timezone(listing)
        if first is not None and not 2000 <= first.year <= 2999:
            raise exceptions.ValidationError({'from': ['Out of range.']})
        start = datetime.combine(first or timezone.now().astimezone(tz).date(), datetime.min.time(), tzinfo=tz)
        rows = V.slots(listing, start, start + timedelta(days=days), request.user)
        return Response(SlotListSerializer({'timezone': tz.key, 'slots': [
            {'id': s.id, 'start': s.start, 'end': s.end, 'local_start': s.start.astimezone(tz).strftime('%Y-%m-%dT%H:%M')}
            for s in rows]}).data)


# ── The visitor's viewings ────────────────────────────────────────────────────

class ViewingSerializer(serializers.ModelSerializer):
    """What the visitor sees: never the agent's internal notes or contact details."""
    listing = serializers.UUIDField(source='property_id', allow_null=True)
    thread = serializers.UUIDField(source='enquiry_id', allow_null=True)
    title = serializers.CharField(source='property.title', default=None, allow_null=True)
    agent_name = serializers.CharField(source='agent.display_name', default=None, allow_null=True)
    local_start = serializers.SerializerMethodField()
    timezone = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = ['id', 'listing', 'title', 'thread', 'agent_name', 'scheduled_at', 'local_start', 'timezone',
                  'duration_minutes', 'status', 'location', 'meeting_url', 'participants', 'created_at']
        read_only_fields = fields

    def get_local_start(self, a) -> str:
        return a.scheduled_at.astimezone(V.zone(a)).strftime('%Y-%m-%dT%H:%M')

    def get_timezone(self, a) -> str:
        return V.zone(a).key


class BookSerializer(serializers.Serializer):
    slot_id = serializers.CharField(max_length=60)
    contact_consent = serializers.BooleanField(help_text=E.CONSENT_TEXT)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    participants = serializers.ListField(child=serializers.CharField(max_length=80), max_length=5, required=False,
                                         default=list, help_text='Names of people who come along')

    def validate_contact_consent(self, value):
        if value is not True:
            raise serializers.ValidationError('Consent to share your contact details is required.')
        return value


class MoveSerializer(serializers.Serializer):
    slot_id = serializers.CharField(max_length=60)


class FeedbackSerializer(serializers.ModelSerializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')

    class Meta:
        model = ViewingFeedback
        fields = ['side', 'rating', 'interested', 'notes', 'updated_at']
        read_only_fields = ['side', 'updated_at']


def save_feedback(request, appt, side):
    s = FeedbackSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    try:
        fb = V.give_feedback(appt, request.user, side, **s.validated_data)
    except V.ViewingError as exc:
        raise ViewingRefused(exc) from exc
    return Response(FeedbackSerializer(fb).data)


def _base():
    return Appointment.objects.select_related('property', 'agent', 'lead__organization')


class ViewingListView(ListAPIView):
    """The caller's viewings, latest first; POST books a slot."""
    permission_classes = [IsAuthenticated]
    serializer_class = ViewingSerializer

    def get_throttles(self):
        return [ThreadMessageThrottle()] if self.request.method == 'POST' else super().get_throttles()

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Appointment.objects.none()
        return _base().filter(lead__user=self.request.user).order_by('-scheduled_at')

    @extend_schema(request=BookSerializer, parameters=[IDEMPOTENCY_PARAMETER],
                   responses={201: ViewingSerializer, 404: OpenApiResponse(description='Listing not available'),
                              409: OpenApiResponse(description='Slot taken or not allowed')})
    @idempotent
    def post(self, request):
        s = BookSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            listing_id, start = V.parse_slot_id(d['slot_id'])
            appt = V.book(request.user, _listing(listing_id), start, note=d['note'], participants=d['participants'])
        except V.ViewingError as exc:
            raise ViewingRefused(exc) from exc
        except E.EnquiryError as exc:
            raise EnquiryRefused(exc) from exc
        return Response(ViewingSerializer(_base().get(pk=appt.pk)).data, status=status.HTTP_201_CREATED)


class ViewingView(APIView):
    permission_classes = [IsAuthenticated]

    def viewing(self, request, pk) -> Appointment:
        appt = _base().filter(pk=pk, lead__user=request.user).first()
        if appt is None:
            raise exceptions.NotFound('Viewing not found.')
        return appt

    def answer(self, pk):
        return Response(ViewingSerializer(_base().get(pk=pk)).data)


class ViewingDetailView(ViewingView):
    @extend_schema(responses=ViewingSerializer)
    def get(self, request, pk):
        return self.answer(self.viewing(request, pk).pk)


class ViewingCancelView(ViewingView):
    @extend_schema(request=None, responses={200: ViewingSerializer, 409: OpenApiResponse(description='Past or closed')})
    def post(self, request, pk):
        try:
            V.cancel(self.viewing(request, pk))
        except V.ViewingError as exc:
            raise ViewingRefused(exc) from exc
        return self.answer(pk)


class ViewingRescheduleView(ViewingView):
    """Move to another free slot of the same listing."""

    @extend_schema(request=MoveSerializer, responses={200: ViewingSerializer,
                                                      409: OpenApiResponse(description='Slot taken or closed')})
    def post(self, request, pk):
        appt = self.viewing(request, pk)
        s = MoveSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            listing_id, start = V.parse_slot_id(s.validated_data['slot_id'])
            if listing_id != appt.property_id:
                raise V.ViewingError('This slot belongs to another listing.', 'slot_unknown')
            V.reschedule(appt, request.user, start)
        except V.ViewingError as exc:
            raise ViewingRefused(exc) from exc
        return self.answer(pk)


class ViewingFeedbackView(ViewingView):
    """The visitor's own feedback; sending it again replaces it."""

    @extend_schema(request=FeedbackSerializer, responses=FeedbackSerializer)
    def post(self, request, pk):
        return save_feedback(request, self.viewing(request, pk), ViewingFeedback.Side.USER)
