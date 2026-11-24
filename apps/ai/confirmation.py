"""
Durable server-side confirmation of side-effect tools (T-2012, ADR-030, FR-AI-006).

Flow: the model calls a ``requires_confirmation`` tool → :func:`propose` stores a
``PendingAction`` and the turn ends with its summary, verbatim → the user answers.
:func:`resolve_reply` (next message) or the pending-actions API then runs the
*stored* arguments through :func:`confirm`. The model never executes such a tool.

* A bare "yes" counts only as the very next message after the summary; anything
  else closes that window (the API can still confirm by id until expiry).
* One open action per conversation (org + phone); a new proposal replaces it.
* Execution is at most once: the row is claimed (pending → executing) before the
  tool runs, so a redelivered message or a concurrent click cannot repeat it.
* ``@guarded`` on the tool functions applies the same gate to direct callers.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import re
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ._tools_context import (
    _ctx_conversation, _ctx_grant, _ctx_interactive, _ctx_org, _ctx_phone, _ctx_turn, _ctx_user, set_context,
)

logger = logging.getLogger(__name__)

# Whole-message matches only: "yes but how much is the fee?" is not a confirmation.
_YES = (r"yes|yeah|yep|y|ok|okay|confirm(?:ed)?|sure|go ahead|proceed|do it|please|"
        r"haan|han|haa|ha|ji|jee|theek(?: hai)?|thik(?: hai)?|kar do|kardo|bilkul|"
        r"ہاں|جی|ٹھیک(?: ہے)?")
_NO = (r"no|nope|nah|cancel|stop|don'?t|do not|thanks|thank you|please|"
       r"nahi|nahin|mat karo|rehne do|نہیں|منسوخ")
_SEP = r"[\s,.!*_👍🙏✅❌]+"
_YES_RE = re.compile(rf"(?:{_YES})(?:{_SEP}(?:{_YES}))*", re.I)
_NO_RE = re.compile(rf"(?:{_NO})(?:{_SEP}(?:{_NO}))*", re.I)
_COURTESY = {'please', 'thanks', 'thank you'}


class NotConfirmable(Exception):
    """The action is expired, already resolved, or replaced."""
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def _clean(message: str) -> str:
    text = re.sub(r'[\u200b-\u200f\ufe0f]', '', message or '').lower()
    text = text.replace('\u2019', "'")
    return re.sub(rf'^{_SEP}|{_SEP}$', '', text)


def _matches(pattern, message: str) -> bool:
    text = _clean(message)
    return bool(text) and len(text) <= 60 and text not in _COURTESY and bool(pattern.fullmatch(text))


def is_affirmative(message: str) -> bool:
    return _matches(_YES_RE, message)


def is_negative(message: str) -> bool:
    return _matches(_NO_RE, message)


def conversation_key(phone: str, org=None) -> str:
    current = _ctx_conversation.get()
    if current is not None:
        return current.key
    return f"{org.pk if org is not None else 'global'}:{phone}"


def args_hash(tool: str, args: dict) -> str:
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True, default=str).encode()).hexdigest()


def _open(phone: str, org=None):
    from .models import PendingAction
    return PendingAction.objects.filter(conversation_key=conversation_key(phone, org),
                                        status=PendingAction.Status.PENDING)


# ── Propose ───────────────────────────────────────────────────────────────────

def propose(spec, args: dict) -> dict:
    """Store the proposed call; return the tool result that asks the user."""
    from .models import PendingAction
    from .proposals import Refused, describe

    phone = _ctx_phone.get() or ''
    if not phone or not _ctx_interactive.get():
        return {'success': False,
                'error': 'This action needs the user\'s own confirmation and is not available here.'}
    try:
        question, summary = describe(spec.name, args)
    except Refused as exc:
        return {'success': False, 'error': str(exc)}

    org = _ctx_org.get()
    user = _ctx_user.get()
    prompt = f'{question}\n\n{summary}\n\nReply *YES* to confirm or *NO* to cancel.'
    fields = dict(
        conversation_key=conversation_key(phone, org), organization=org,
        conversation=_ctx_conversation.get(),
        user=user if getattr(user, 'pk', None) else None, turn_id=_ctx_turn.get() or '',
        tool=spec.name, side_effect=spec.side_effect, args=json.dumps(args),
        args_hash=args_hash(spec.name, args), prompt=prompt,
        expires_at=timezone.now() + timedelta(minutes=settings.AI_PENDING_ACTION_TTL_MIN))
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                _open(phone, org).update(status=PendingAction.Status.SUPERSEDED,
                                         awaiting_reply=False, resolved_at=timezone.now())
                action = PendingAction.objects.create(**fields)
            break
        except IntegrityError:            # concurrent proposal in this conversation
            if attempt == 2:
                raise
    logger.info('ai.pending proposed id=%s tool=%s', action.pk, spec.name)
    return {'success': False, 'confirmation_required': True, 'pending_action_id': str(action.pk),
            'summary': summary, 'message': prompt, 'whatsapp_summary': prompt}


def guarded(fn):
    """Direct calls of a side-effect tool propose; only :func:`confirm` executes it."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        bound = signature.bind(*a, **kw)
        args = dict(bound.arguments)
        if _ctx_grant.get() == args_hash(fn.__name__, args):
            _ctx_grant.set('')            # single use
            return fn(**args)
        from .tool_registry import REGISTRY
        return propose(REGISTRY[fn.__name__], args)
    return wrapper


# ── Resolve ───────────────────────────────────────────────────────────────────

_RESULT_KEYS = ('success', 'found', 'listing_id', 'deal_id', 'audit_id', 'agent_id', 'enquiry_id',
                'appointment_id')


def confirm(action, via: str) -> dict:
    """Claim and run the stored call. Raises :class:`NotConfirmable`."""
    from .models import PendingAction
    S = PendingAction.Status
    now = timezone.now()
    claimed = PendingAction.objects.filter(
        pk=action.pk, status=S.PENDING, expires_at__gt=now,
    ).update(status=S.EXECUTING, confirmed_at=now, confirmed_via=via, awaiting_reply=False)
    if not claimed:
        PendingAction.objects.filter(pk=action.pk, status=S.PENDING, expires_at__lte=now).update(
            status=S.EXPIRED, awaiting_reply=False, resolved_at=now)
        action.refresh_from_db()
        raise NotConfirmable(action.status)

    started = time.monotonic()
    try:
        result = _execute(action)
    except Exception as exc:
        logger.error('ai.pending id=%s tool=%s raised: %s', action.pk, action.tool, exc, exc_info=True)
        result = {'success': False, 'error': 'The action failed. Please try again later.'}
    if not isinstance(result, dict):
        result = {'success': True}
    ok = not result.get('error') and result.get('success') is not False
    outcome = {k: result[k] for k in _RESULT_KEYS if k in result}
    outcome['ms'] = int((time.monotonic() - started) * 1000)
    PendingAction.objects.filter(pk=action.pk).update(
        status=S.EXECUTED if ok else S.FAILED, result=outcome, resolved_at=timezone.now())
    action.refresh_from_db()
    logger.info('ai.pending id=%s tool=%s %s via=%s', action.pk, action.tool, action.status, via)
    return result


def _execute(action) -> dict:
    from . import tool_registry as R
    unavailable = {'success': False, 'error': 'This action is no longer available.'}
    market = getattr(action.organization, 'country', None)
    spec = next((s for s in R.enabled_tools(market=market) if s.name == action.tool), None)
    if spec is None:                      # flag turned off since the proposal
        return unavailable
    args = json.loads(action.args)
    if args_hash(action.tool, args) != action.args_hash:
        logger.error('ai.pending id=%s args hash mismatch; refusing', action.pk)
        return unavailable
    token = _ctx_conversation.set(action.conversation)
    set_context(action.user, action.phone, org=action.organization)
    _ctx_grant.set(action.args_hash)
    try:
        return spec.fn(**args)
    finally:
        set_context(None, '')
        _ctx_conversation.reset(token)


def decline(action) -> None:
    from .models import PendingAction
    S = PendingAction.Status
    if not PendingAction.objects.filter(pk=action.pk, status=S.PENDING).update(
            status=S.DECLINED, awaiting_reply=False, resolved_at=timezone.now()):
        action.refresh_from_db()
        raise NotConfirmable(action.status)
    action.refresh_from_db()


def result_text(result: dict) -> str:
    return (result.get('whatsapp_summary') or result.get('message') or result.get('error')
            or 'Done.')


def resolve_reply(phone: str, message: str, user=None, org=None) -> str | None:
    """
    Handle the message that follows a proposal. Returns the reply when it was a
    plain yes/no to it, else None (and the reply window is closed).
    """
    action = _open(phone, org).filter(awaiting_reply=True).select_related('organization', 'user').first()
    if action is None:
        return None
    if action.user_id != getattr(user, 'pk', None):
        close_reply_window(phone, org)
        return None
    try:
        if is_affirmative(message):
            return result_text(confirm(action, 'message'))
        if is_negative(message):
            decline(action)
            return "Okay, I've cancelled that. Nothing was done."
    except NotConfirmable as exc:
        if exc.status == 'expired':
            return 'That request has expired, so nothing was done. Please ask again if you still want it.'
        return None
    close_reply_window(phone, org)
    return None


def close_reply_window(phone: str, org=None) -> None:
    """The conversation moved on: a later bare "yes" must not confirm the action."""
    _open(phone, org).filter(awaiting_reply=True).update(awaiting_reply=False)


def withdraw(phone: str, org=None) -> None:
    """Cancel the open action (conversation reset, or its summary was never shown)."""
    from .models import PendingAction
    _open(phone, org).update(status=PendingAction.Status.SUPERSEDED, awaiting_reply=False,
                             resolved_at=timezone.now())


def proposed_since(phone: str, org, since):
    return _open(phone, org).filter(created_at__gte=since).first()


# ── Housekeeping ──────────────────────────────────────────────────────────────

def purge() -> int:
    """Expire overdue rows, fail interrupted ones, delete those past retention."""
    from .models import PendingAction
    S = PendingAction.Status
    now = timezone.now()
    PendingAction.objects.filter(status=S.PENDING, expires_at__lte=now).update(
        status=S.EXPIRED, awaiting_reply=False, resolved_at=now)
    PendingAction.objects.filter(status=S.EXECUTING, confirmed_at__lt=now - timedelta(hours=1)).update(
        status=S.FAILED, result={'interrupted': True}, resolved_at=now)
    cutoff = now - timedelta(days=settings.AI_PENDING_ACTION_RETENTION_DAYS)
    deleted, _ = PendingAction.objects.filter(created_at__lt=cutoff).delete()
    return deleted
