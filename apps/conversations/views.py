"""
Conversation API (T-2020, FR-AI-001): the signed-in user's own assistant chats.

A message is answered as JSON, or as server-sent events with ``stream: true``.
The grounding check needs the whole reply, so nothing is sent token by token:
the stream is ``accepted`` → ``pending_action``? → ``card``* → ``final`` (or ``error``).
"""
import json
import logging

from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.http import StreamingHttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.generics import ListAPIView, get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.core.throttles import AiQueryThrottle

from . import store
from .models import Conversation, ConversationState, Message

logger = logging.getLogger(__name__)

MAX_TEXT = 2000
MAX_SHORTLIST = 50
MAX_OPEN = 50
LOCK_SECONDS = 120


class AiDailyThrottle(UserRateThrottle):
    """Per-user daily cap on assistant messages (AI spec §11)."""
    scope = 'ai_daily'


class EventStreamRenderer(BaseRenderer):
    """Lets ``Accept: text/event-stream`` through negotiation; errors are still JSON."""
    media_type = 'text/event-stream'
    format = 'event-stream'
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return JSONRenderer().render(data)


class Conflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT

    def __init__(self, detail, code):
        super().__init__(detail, code)


# ── Serializers ───────────────────────────────────────────────────────────────

class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ['id', 'channel', 'language', 'is_open', 'created_at', 'last_message_at']
        read_only_fields = ['id', 'channel', 'is_open', 'created_at', 'last_message_at']


class ListingCardSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    city = serializers.CharField()
    location = serializers.CharField()
    price = serializers.IntegerField(allow_null=True)
    currency = serializers.CharField()
    price_type = serializers.CharField()
    purpose = serializers.CharField()
    size = serializers.CharField(allow_blank=True)
    bedrooms = serializers.IntegerField(allow_null=True)
    bathrooms = serializers.IntegerField(allow_null=True)
    verified = serializers.BooleanField()


class MessagePendingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    tool = serializers.CharField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()


class MessageSerializer(serializers.ModelSerializer):
    pending_action = MessagePendingSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Message
        fields = ['id', 'seq', 'role', 'content', 'status', 'citations', 'pending_action', 'created_at']
        read_only_fields = fields


class SendMessageSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=MAX_TEXT)
    client_message_id = serializers.UUIDField(required=False, help_text='Retry-safe id chosen by the client')
    stream = serializers.BooleanField(default=False)


class TurnSerializer(serializers.Serializer):
    message = MessageSerializer()
    cards = ListingCardSerializer(many=True)


class StateSerializer(serializers.ModelSerializer):
    shortlist = serializers.ListField(child=serializers.UUIDField(), max_length=MAX_SHORTLIST)

    class Meta:
        model = ConversationState
        fields = ['active_criteria', 'last_results', 'shortlist', 'updated_at']
        read_only_fields = ['active_criteria', 'last_results', 'updated_at']

    def validate_shortlist(self, ids):
        from apps.properties.models import Property
        ids = list(dict.fromkeys(str(i) for i in ids))
        org = self.instance.conversation.organization
        visible = {str(p) for p in Property.objects.visible_to(org).filter(pk__in=ids).values_list('pk', flat=True)}
        if missing := [i for i in ids if i not in visible]:
            raise serializers.ValidationError(f'Unknown or unavailable listings: {", ".join(missing)}')
        return ids


# ── Helpers ───────────────────────────────────────────────────────────────────

def _own(request, pk) -> Conversation:
    return get_object_or_404(Conversation.objects.select_related('organization', 'user'),
                             pk=pk, user=request.user)


def cards(ids, org=None) -> list[dict]:
    """Current facts of the cited listings that are still visible, in citation order."""
    import uuid

    from apps.properties.models import Property
    valid = []
    for i in ids:
        try:
            valid.append(str(uuid.UUID(str(i))))
        except ValueError:                # third-party result
            continue
    found = {str(p.pk): p for p in Property.objects.visible_to(org).filter(pk__in=valid)}
    return ListingCardSerializer([{
        'id': p.pk, 'title': p.title, 'city': p.city, 'location': p.location,
        'price': None if p.price_on_request else p.price, 'currency': p.currency,
        'price_type': p.price_type, 'purpose': p.purpose,
        'size': f'{float(p.size_value):g} {p.size_unit}' if p.size_value and p.size_unit else '',
        'bedrooms': p.bedrooms, 'bathrooms': p.bathrooms,
        'verified': p.legal_status == Property.LegalStatus.VERIFIED,
    } for i in valid if (p := found.get(i))], many=True).data


def _turn_payload(conv, answer) -> dict:
    return {'message': MessageSerializer(answer).data, 'cards': cards(answer.citations, conv.organization)}


# ── Views ─────────────────────────────────────────────────────────────────────

class ConversationListView(ListAPIView):
    """List the caller's conversations (every channel), newest first; POST opens a web chat."""
    permission_classes = [IsAuthenticated]
    serializer_class = ConversationSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Conversation.objects.none()
        return Conversation.objects.filter(user=self.request.user)

    @extend_schema(request=ConversationSerializer, responses={201: ConversationSerializer})
    def post(self, request):
        data = ConversationSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        if Conversation.objects.filter(user=request.user, channel=Conversation.Channel.WEB,
                                       is_open=True).count() >= MAX_OPEN:
            raise Conflict('Too many open conversations. Delete one first.', 'too_many_conversations')
        conv = store.open_web(request.user, data.validated_data.get('language', ''))
        return Response(ConversationSerializer(conv).data, status=status.HTTP_201_CREATED)


class ConversationDetailView(APIView):
    """Read or permanently delete one conversation."""
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=ConversationSerializer)
    def get(self, request, pk):
        return Response(ConversationSerializer(_own(request, pk)).data)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        store.delete(_own(request, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationStateView(APIView):
    """Session state: last search, its results, and the shortlist (the only editable field)."""
    permission_classes = [IsAuthenticated]

    def _state(self, request, pk):
        return ConversationState.objects.select_related('conversation__organization').get_or_create(
            conversation=_own(request, pk))[0]

    @extend_schema(responses=StateSerializer)
    def get(self, request, pk):
        return Response(StateSerializer(self._state(request, pk)).data)

    @extend_schema(request=StateSerializer, responses=StateSerializer)
    def patch(self, request, pk):
        state = self._state(request, pk)
        data = StateSerializer(state, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        if 'shortlist' in data.validated_data:
            state.shortlist = data.validated_data['shortlist']
            state.save(update_fields=['shortlist', 'updated_at'])
        return Response(StateSerializer(state).data)


class MessageListView(ListAPIView):
    """Messages of one conversation, oldest first; POST sends one to the assistant."""
    permission_classes = [IsAuthenticated]
    serializer_class = MessageSerializer
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [AiQueryThrottle(), AiDailyThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Message.objects.none()
        return (Message.objects.filter(conversation=_own(self.request, self.kwargs['pk']))
                .select_related('pending_action').order_by('seq'))

    @extend_schema(request=SendMessageSerializer, responses={
        200: TurnSerializer, 409: OpenApiResponse(description='Closed, not a web chat, or a turn is in progress')})
    def post(self, request, pk):
        conv = _own(request, pk)
        data = SendMessageSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        from apps.whatsapp.models import WhatsAppSession
        if WhatsAppSession.objects.filter(user=request.user,
                                          conversation_mode=WhatsAppSession.ConversationMode.BLOCKED).exists():
            raise exceptions.PermissionDenied('Your account is suspended pending compliance review.')
        if conv.channel != Conversation.Channel.WEB:
            raise Conflict('Reply to this conversation on WhatsApp.', 'wrong_channel')
        if not conv.is_open:
            raise Conflict('This conversation is closed.', 'conversation_closed')

        lock = f'ai:turn:{conv.pk}'
        if not cache.add(lock, 1, LOCK_SECONDS):
            raise Conflict('The previous message is still being answered.', 'turn_in_progress')
        try:
            message, created = store.begin_turn(conv, data.validated_data['text'],
                                                data.validated_data.get('client_message_id'))
            answer = None
            if not created:
                if message.content != data.validated_data['text']:
                    raise Conflict('client_message_id was used for another message.', 'client_message_id_reused')
                answer = Message.objects.filter(reply_to=message).first()
                if answer is None and message.status == Message.Status.PROCESSING and not store.is_stale(message):
                    raise Conflict('The previous message is still being answered.', 'turn_in_progress')
        except Exception:
            cache.delete(lock)
            raise

        if data.validated_data['stream']:
            response = StreamingHttpResponse(_events(conv, message, answer, request.user, lock),
                                             content_type='text/event-stream')
            response['Cache-Control'] = 'no-cache'
            response['X-Accel-Buffering'] = 'no'
            return response
        try:
            answer = answer or _run(conv, message, request.user)
        except Exception as exc:
            logger.error('conversation turn failed: %s', exc, exc_info=True)
            raise TurnFailed() from exc
        finally:
            cache.delete(lock)
        return Response(_turn_payload(conv, answer))


class TurnFailed(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = 'The assistant could not answer. Please try again.'
    default_code = 'turn_failed'


def _run(conv, message, user) -> Message:
    from apps.ai.service import get_service_manager
    phone = (user.phone or '').lstrip('+')
    get_service_manager().process(phone, message.content, user, organization=conv.organization,
                                  conversation=conv, user_message=message)
    return Message.objects.select_related('pending_action').get(reply_to=message)


def _sse(event: str, data) -> str:
    return f'event: {event}\ndata: {json.dumps(data, cls=DjangoJSONEncoder)}\n\n'


def _events(conv, message, answer, user, lock):
    try:
        yield _sse('accepted', {'message_id': str(message.pk)})
        try:
            payload = _turn_payload(conv, answer or _run(conv, message, user))
        except Exception as exc:
            logger.error('conversation turn failed: %s', exc, exc_info=True)
            yield _sse('error', {'code': TurnFailed.default_code, 'message': TurnFailed.default_detail})
            return
        if payload['message']['pending_action']:
            yield _sse('pending_action', payload['message']['pending_action'])
        for card in payload['cards']:
            yield _sse('card', card)
        yield _sse('final', payload['message'])
    finally:
        cache.delete(lock)
