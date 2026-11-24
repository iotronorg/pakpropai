"""Enquiries and their threads (T-2050). Every endpoint is for the signed-in user only."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_field
from rest_framework import exceptions, serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.core.idempotency import IDEMPOTENCY_PARAMETER, idempotent
from apps.leads.models import ConversationMessage
from apps.properties.public_listings import redact_contacts

from . import services as S
from .models import Enquiry

MAX_TEXT = 2000
_STATUS = {'limit': 429, 'blocked': 403}


class ThreadMessageThrottle(UserRateThrottle):
    scope = 'thread_message'


class EnquiryRefused(exceptions.APIException):
    def __init__(self, exc: S.EnquiryError):
        self.status_code = _STATUS.get(exc.code, 409)
        super().__init__(str(exc), f'enquiry.{exc.code}')


class RequirementSerializer(serializers.Serializer):
    intent = serializers.ChoiceField(choices=['buy', 'rent', 'sell', 'invest'])
    city = serializers.CharField(max_length=100)
    details = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


class EnquiryCreateSerializer(serializers.Serializer):
    listing_id = serializers.UUIDField(required=False)
    requirement = RequirementSerializer(required=False)
    message = serializers.CharField(max_length=MAX_TEXT)
    contact_consent = serializers.BooleanField(help_text=S.CONSENT_TEXT)
    preferred_channel = serializers.ChoiceField(choices=Enquiry.Channel.choices, default=Enquiry.Channel.IN_APP)

    def validate_contact_consent(self, value):
        if value is not True:
            raise serializers.ValidationError('Consent to share your contact details is required.')
        return value

    def validate(self, data):
        if ('listing_id' in data) == ('requirement' in data):
            raise serializers.ValidationError('Send either listing_id or requirement.')
        return data


class CounterpartSerializer(serializers.Serializer):
    name = serializers.CharField(allow_null=True)
    phone = serializers.CharField(allow_null=True)


class ThreadSerializer(serializers.ModelSerializer):
    """The same shape for both sides; ``side`` says which one the caller is on."""
    side = serializers.SerializerMethodField()
    counterpart = serializers.SerializerMethodField()
    unread = serializers.SerializerMethodField()
    sharing = serializers.SerializerMethodField(help_text='The caller shares their contact details')
    contacts_shared = serializers.BooleanField(read_only=True, help_text='Both sides share')

    class Meta:
        model = Enquiry
        fields = ['id', 'side', 'kind', 'status', 'listing', 'subject', 'requirement', 'preferred_channel',
                  'counterpart', 'unread', 'sharing', 'contacts_shared', 'created_at', 'last_message_at']
        read_only_fields = fields

    def _side(self, e):
        return S.USER if e.user_id == self.context['request'].user.pk else S.RESPONDER

    def get_side(self, e) -> str:
        return self._side(e)

    @extend_schema_field(CounterpartSerializer)
    def get_counterpart(self, e):
        return S.counterpart(e, self._side(e))

    def get_unread(self, e) -> bool:
        last, read = ((e.responder_last_at, e.user_read_at) if self._side(e) == S.USER
                      else (e.user_last_at, e.responder_read_at))
        return last is not None and (read is None or read < last)

    def get_sharing(self, e) -> bool:
        return (e.consent_revoked_at is None if self._side(e) == S.USER else e.responder_shared_at is not None)

    def to_representation(self, e):
        data = super().to_representation(e)
        if not e.contacts_shared and data['requirement'].get('details'):
            data['requirement'] = {**data['requirement'], 'details': redact_contacts(data['requirement']['details'])}
        return data


class ThreadMessageSerializer(serializers.ModelSerializer):
    side = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField(help_text='Contact details are hidden until both sides share them')

    class Meta:
        model = ConversationMessage
        fields = ['id', 'side', 'body', 'created_at']
        read_only_fields = fields

    def get_side(self, m) -> str:
        return S.USER if m.direction == ConversationMessage.Direction.INBOUND else S.RESPONDER

    def get_body(self, m) -> str:
        if self.context['enquiry'].contacts_shared or self.get_side(m) == self.context['side']:
            return m.body
        return redact_contacts(m.body)


class PostMessageSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=MAX_TEXT)


class SharingSerializer(serializers.Serializer):
    share = serializers.BooleanField()


def _base():
    return Enquiry.objects.select_related('user', 'organization', 'responder__agent_profile', 'lead__assigned_agent')


class EnquiryListView(ListAPIView):
    """The caller's own enquiries; POST sends one."""
    permission_classes = [IsAuthenticated]
    serializer_class = ThreadSerializer

    def get_throttles(self):
        return [ThreadMessageThrottle()] if self.request.method == 'POST' else super().get_throttles()

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Enquiry.objects.none()
        return _base().filter(user=self.request.user)

    @extend_schema(request=EnquiryCreateSerializer, parameters=[IDEMPOTENCY_PARAMETER],
                   responses={201: ThreadSerializer, 200: OpenApiResponse(ThreadSerializer, 'Added to the open thread'),
                              404: OpenApiResponse(description='Listing not available'),
                              409: OpenApiResponse(description='Own listing or lister unavailable')})
    @idempotent
    def post(self, request):
        s = EnquiryCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        listing = None
        if 'listing_id' in d:
            listing = S.visible_listing(d['listing_id'])
            if listing is None:
                raise exceptions.NotFound('Listing not found.')
        try:
            e, created = S.create(request.user, message=d['message'], listing=listing,
                                  requirement=dict(d.get('requirement') or {}),
                                  preferred_channel=d['preferred_channel'])
        except S.EnquiryError as exc:
            raise EnquiryRefused(exc) from exc
        return Response(ThreadSerializer(_base().get(pk=e.pk), context={'request': request}).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@extend_schema(parameters=[OpenApiParameter('side', OpenApiTypes.STR, enum=[S.USER, S.RESPONDER],
                                            description='responder: enquiries you can answer')])
class ThreadListView(ListAPIView):
    """Threads the caller takes part in (``side=user``, default) or may answer (``side=responder``)."""
    permission_classes = [IsAuthenticated]
    serializer_class = ThreadSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Enquiry.objects.none()
        side = self.request.query_params.get('side', S.USER)
        if side not in (S.USER, S.RESPONDER):
            raise exceptions.ValidationError({'side': ['user or responder.']})
        user = self.request.user
        if side == S.USER:
            return _base().filter(user=user)
        return _base().filter(S.responder_q(user)).exclude(user=user)


class ThreadView(APIView):
    permission_classes = [IsAuthenticated]

    def thread(self, request, pk) -> tuple[Enquiry, str]:
        e = _base().filter(pk=pk).first()
        side = S.side_of(e, request.user) if e else None
        if side is None:
            raise exceptions.NotFound('Thread not found.')
        return e, side

    def answer(self, request, e):
        return Response(ThreadSerializer(_base().get(pk=e.pk), context={'request': request}).data)


class ThreadDetailView(ThreadView):
    @extend_schema(responses=ThreadSerializer)
    def get(self, request, pk):
        return self.answer(request, self.thread(request, pk)[0])


class ThreadMessagesView(ThreadView):
    """Messages, newest first; POST adds one."""

    def get_throttles(self):
        return [ThreadMessageThrottle()] if self.request.method == 'POST' else super().get_throttles()

    @extend_schema(operation_id='threads_messages_list', responses=ThreadMessageSerializer(many=True),
                   parameters=[OpenApiParameter('limit', int), OpenApiParameter('offset', int)])
    def get(self, request, pk):
        from apps.core.query_params import limit_offset
        e, side = self.thread(request, pk)
        limit, offset = limit_offset(request.query_params, default=50, maximum=200)
        rows = e.messages.order_by('-created_at', '-id')[offset:offset + limit]
        return Response(ThreadMessageSerializer(rows, many=True, context={'enquiry': e, 'side': side}).data)

    @extend_schema(request=PostMessageSerializer, parameters=[IDEMPOTENCY_PARAMETER],
                   responses={201: ThreadMessageSerializer, 409: OpenApiResponse(description='Closed')})
    @idempotent
    def post(self, request, pk):
        e, side = self.thread(request, pk)
        s = PostMessageSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            msg = S.post(e, request.user, side, s.validated_data['body'])
        except S.EnquiryError as exc:
            raise EnquiryRefused(exc) from exc
        return Response(ThreadMessageSerializer(msg, context={'enquiry': e, 'side': side}).data,
                        status=status.HTTP_201_CREATED)


class ThreadReadView(ThreadView):
    @extend_schema(request=None, responses=ThreadSerializer)
    def post(self, request, pk):
        e, side = self.thread(request, pk)
        S.mark_read(e, side)
        return self.answer(request, e)


class ThreadSharingView(ThreadView):
    """Start or stop sharing the caller's own contact details."""

    @extend_schema(request=SharingSerializer, responses=ThreadSerializer)
    def post(self, request, pk):
        e, side = self.thread(request, pk)
        s = SharingSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        S.set_sharing(e, side, s.validated_data['share'])
        return self.answer(request, e)


class ThreadCloseView(ThreadView):
    """Either side can close the thread; no more messages are accepted."""

    @extend_schema(request=None, responses=ThreadSerializer)
    def post(self, request, pk):
        e, _ = self.thread(request, pk)
        S.close(e)
        return self.answer(request, e)
