"""Reads and writes of assistant conversations (T-2020, ADR-033)."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from .models import Conversation, ConversationState, Message

logger = logging.getLogger(__name__)

WINDOW = 40                 # messages (20 turns) given to the model
WINDOW_TTL = 24 * 3600
WINDOW_TEXT = 2000          # characters per message in the window
STALE = timedelta(minutes=5)  # a turn still "processing" after this died


def _window_key(conversation_id) -> str:
    return f'ai:window:{conversation_id}'


def _market(org) -> str:
    from apps.markets.services import org_country
    return (org_country(org) or '')[:2]


# ── Conversations ─────────────────────────────────────────────────────────────

def find(phone: str, org=None) -> Conversation | None:
    """The conversation being handled, else the open one for this contact."""
    from apps.ai._tools_context import _ctx_conversation
    from apps.ai.confirmation import conversation_key
    current = _ctx_conversation.get()
    if current is not None:
        return current
    return Conversation.objects.filter(key=conversation_key(phone, org), is_open=True).first()


def open_whatsapp(phone: str, user=None, org=None) -> Conversation:
    from apps.ai.confirmation import conversation_key
    key = conversation_key(phone, org)
    user = user if getattr(user, 'pk', None) else None
    conv = Conversation.objects.filter(key=key, is_open=True).first()
    if conv is not None and user is not None and conv.user_id not in (None, user.pk):
        close(conv)                      # the number changed hands: never show the old chat
        conv = None
    if conv is None:
        try:
            with transaction.atomic():
                conv = Conversation.objects.create(
                    key=key, channel=Conversation.Channel.WHATSAPP, user=user, organization=org,
                    market_code=_market(org), last_message_at=timezone.now())
        except IntegrityError:           # concurrent first message
            conv = Conversation.objects.get(key=key, is_open=True)
    elif conv.user_id is None and user is not None:
        conv.user = user
        conv.save(update_fields=['user'])
    return conv


def open_web(user, language: str = '') -> Conversation:
    conv = Conversation(channel=Conversation.Channel.WEB, user=user, language=language,
                        market_code=_market(None), last_message_at=timezone.now())
    conv.key = f'global:web-{conv.pk}'
    conv.save()
    return conv


def forget(conv: Conversation) -> None:
    """Drop the cached copies of a conversation."""
    cache.delete_many([_window_key(conv.pk), f'ai:grounding:{conv.key}', f'ai:conv:{conv.key}'])


def close(conv: Conversation) -> None:
    Conversation.objects.filter(pk=conv.pk).update(is_open=False)
    forget(conv)


def reset(phone: str, org=None) -> None:
    conv = find(phone, org)
    if conv is not None:
        close(conv)


# ── Window (model history) ────────────────────────────────────────────────────

def window(conv: Conversation | None) -> list[dict]:
    """Last completed turns, oldest first: ``[{'role': 'user'|'model', 'text'}]``."""
    if conv is None:
        return []
    data = cache.get(_window_key(conv.pk))
    if isinstance(data, list):
        return data
    rows = list(Message.objects.filter(conversation=conv, in_window=True, status=Message.Status.DONE)
                .order_by('-seq')[:WINDOW])
    data = [_entry(m) for m in reversed(rows)]
    cache.set(_window_key(conv.pk), data, WINDOW_TTL)
    return data


def _entry(m: Message) -> dict:
    return {'role': 'user' if m.role == Message.Role.USER else 'model', 'text': m.content[:WINDOW_TEXT]}


# ── Turns ─────────────────────────────────────────────────────────────────────

def _insert(conv: Conversation, **fields) -> Message:
    """Next ``seq``; the conversation row is locked so numbers never collide."""
    with transaction.atomic():
        Conversation.objects.select_for_update().get(pk=conv.pk)
        last = Message.objects.filter(conversation=conv).aggregate(m=Max('seq'))['m'] or 0
        return Message.objects.create(conversation=conv, seq=last + 1, **fields)


def begin_turn(conv: Conversation, text: str, client_id=None) -> tuple[Message, bool]:
    """
    Store the user's message before processing. ``(message, created)``; a repeated
    ``client_id`` returns the first message (``created=False``).
    """
    if client_id is not None:
        existing = Message.objects.filter(conversation=conv, client_id=client_id).first()
        if existing is not None:
            return existing, False
    try:
        with transaction.atomic():
            return _insert(conv, role=Message.Role.USER, content=text, client_id=client_id,
                           status=Message.Status.PROCESSING), True
    except IntegrityError:
        if client_id is None:
            raise
        return Message.objects.get(conversation=conv, client_id=client_id), False


def finish_turn(conv: Conversation, message: Message, reply: str, *, trace=None, in_window=True,
                citations=(), criteria=None, results=None, language='') -> Message:
    pending = None
    if trace is not None and trace.tool_calls:
        pending = next((c.get('pending_action') for c in trace.tool_calls if c.get('pending_action')), None)
    if pending is None:
        action = proposed_since_key(conv, message, reply)
        pending = action.pk if action else None
    with transaction.atomic():
        Message.objects.filter(pk=message.pk).update(status=Message.Status.DONE, in_window=in_window)
        answer = _insert(conv, role=Message.Role.ASSISTANT, content=reply, reply_to=message, trace=trace,
                         in_window=in_window, citations=list(citations)[:20], pending_action_id=pending)
        fields = {'last_message_at': timezone.now()}
        if language:
            fields['language'] = language[:10]
        Conversation.objects.filter(pk=conv.pk).update(**fields)
        if results is not None:
            ConversationState.objects.update_or_create(
                conversation=conv, defaults={'active_criteria': criteria or {}, 'last_results': results[:20]})
    cache.delete(_window_key(conv.pk))       # rebuilt from the database on the next turn
    return answer


def proposed_since_key(conv: Conversation, message: Message, reply: str):
    """The open action whose summary this reply is (direct routes have no tool-call record)."""
    from apps.ai.models import PendingAction
    action = PendingAction.objects.filter(conversation_key=conv.key, status=PendingAction.Status.PENDING,
                                          created_at__gte=message.created_at).first()
    return action if action is not None and action.prompt == reply else None


def fail_turn(message: Message) -> None:
    Message.objects.filter(pk=message.pk).update(status=Message.Status.FAILED, in_window=False)


def add_exchange(phone: str, org, user, text: str, reply: str) -> None:
    """Image/document turns, which do not go through the service. Never raises."""
    try:
        with transaction.atomic():
            conv = open_whatsapp(phone, user, org)
            message, _ = begin_turn(conv, text)
            finish_turn(conv, message, reply)
    except Exception as exc:
        logger.error('conversation write failed: %s', exc, exc_info=True)


def is_stale(message: Message) -> bool:
    return message.created_at < timezone.now() - STALE


# ── Privacy ───────────────────────────────────────────────────────────────────

def delete(conv: Conversation, unlink: bool = False) -> None:
    """Delete a conversation with its messages, state, actions and trace link."""
    from apps.ai.models import AITrace
    from apps.core.crypto import blind_index
    forget(conv)
    # A WhatsApp key is shared by the contact's other conversations.
    if unlink or conv.channel == Conversation.Channel.WEB:
        AITrace.objects.filter(conversation_ref=blind_index(conv.key)).update(conversation_ref='')
    conv.delete()


def erase_user(user) -> int:
    from django.db.models import Q

    phone = (getattr(user, 'phone', '') or '').lstrip('+')
    mine = Q(user=user)
    if phone:                              # rows written before the user was linked
        mine |= Q(user__isnull=True) & (Q(key__endswith=f':{phone}') | Q(key__endswith=f':+{phone}'))
    n = 0
    for conv in Conversation.objects.filter(mine):
        delete(conv, unlink=True)
        n += 1
    return n


def purge() -> int:
    """Retention per market: ``Market.settings['conversation_retention_days']``, else the default."""
    from apps.markets.models import Market
    now = timezone.now()
    default = settings.CONVERSATION_RETENTION_DAYS
    own = {}
    for code, conf in Market.objects.values_list('code', 'settings'):
        days = (conf or {}).get('conversation_retention_days')
        if isinstance(days, int) and not isinstance(days, bool) and days > 0:
            own[code] = days
    expired = [Conversation.objects.filter(market_code=c, last_message_at__lt=now - timedelta(days=d))
               for c, d in own.items()]
    expired.append(Conversation.objects.exclude(market_code__in=own)
                   .filter(last_message_at__lt=now - timedelta(days=default)))
    n = 0
    for qs in expired:
        for conv in qs.iterator(chunk_size=500):
            delete(conv)
            n += 1
    return n
