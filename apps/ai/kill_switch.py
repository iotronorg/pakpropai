"""
AI kill switch per platform, market and organization (T-2070, ADR-043, MVP M12).

Checked at three points, so a forgotten call site still cannot reach a model:

* ``AIServiceManager``: the turn ends with :func:`paused_reply` (no tools, no
  confirmations); a draft nobody is waiting for gets ``''``.
* ``apps.ai.llm`` (every completion, chat and vision call): :class:`AIStopped`.
* Calls that use a provider directly (document OCR, speech to text).

The state is read from the database every time: a stop takes effect on the next
call everywhere. If it cannot be read the call fails, so it fails closed.
"""
from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


class AlreadyStopped(ValueError):
    """A stop for this target is already in force."""


class AlreadyReleased(ValueError):
    pass


# ── State ─────────────────────────────────────────────────────────────────────

_scope: contextvars.ContextVar[tuple] = contextvars.ContextVar('ai_kill_scope', default=(None, ''))


@contextmanager
def scope(organization=None, market: str | None = ''):
    """Who the model calls inside the block are made for."""
    token = _scope.set((organization, market or ''))
    try:
        yield
    finally:
        _scope.reset(token)


def active(organization=None, market: str | None = ''):
    """The stop in force for a call, or None. Missing values come from the surrounding scope, else the default market."""
    from apps.markets.services import default_market_code, org_country

    from .models import AIKillSwitch
    ctx_org, ctx_market = _scope.get()
    if organization is None:                    # a market alone does not leave the surrounding organization
        organization, market = ctx_org, market or ctx_market
    org_id = getattr(organization, 'pk', None)
    markets = {m.upper() for m in (market, getattr(organization, 'market_id', None)) if m}
    if org_id:
        markets.add(org_country(organization))
    markets = markets or {default_market_code()}
    match = Q(scope=AIKillSwitch.Scope.PLATFORM) | Q(market_id__in=markets)
    if org_id:
        match |= Q(organization_id=org_id)
    return AIKillSwitch.objects.filter(match, released_at__isnull=True).first()


def is_stopped(organization=None, market: str | None = '') -> bool:
    return active(organization, market) is not None


# ── Changes ───────────────────────────────────────────────────────────────────

def _audit(switch, actor, detail, request=None):
    from apps.core.models import AuditLog
    AuditLog.log(actor=actor if getattr(actor, 'pk', None) else None, action=AuditLog.Action.CONFIG, target=switch,
                 detail=detail, request=request,
                 after={'scope': switch.scope, 'market': switch.market_id,
                        'organization': str(switch.organization_id) if switch.organization_id else None,
                        'reason': switch.reason, 'released': switch.released_at is not None})


def stop(*, reason: str, market=None, organization=None, actor=None, request=None):
    from .models import AIKillSwitch
    S = AIKillSwitch.Scope
    kind = S.ORGANIZATION if organization is not None else S.MARKET if market is not None else S.PLATFORM
    try:
        with transaction.atomic():
            switch = AIKillSwitch.objects.create(scope=kind, market=market, organization=organization, reason=reason,
                                                 created_by=actor if getattr(actor, 'pk', None) else None)
            _audit(switch, actor, f'AI stopped: {switch}', request)
    except IntegrityError:
        raise AlreadyStopped('The assistant is already stopped there.') from None
    logger.critical('ai.kill_switch stopped %s', switch)
    return switch


def release(switch, *, actor=None, request=None):
    from .models import AIKillSwitch
    with transaction.atomic():
        row = AIKillSwitch.objects.select_for_update().get(pk=switch.pk)
        if row.released_at is not None:
            raise AlreadyReleased('This stop was already released.')
        row.released_at = timezone.now()
        row.released_by = actor if getattr(actor, 'pk', None) else None
        row.save(update_fields=['released_at', 'released_by'])
        _audit(row, actor, f'AI stop released: {row}', request)
    logger.warning('ai.kill_switch released %s', row)
    return row


# ── What the user sees ────────────────────────────────────────────────────────

_PAUSED = {
    'en': 'Our assistant is paused right now, so nothing was done with your message. '
          'Please try again later, or contact the agency directly.',
    'roman': 'Hamara assistant abhi band hai, is liye aap ke message par koi amal nahi hua. '
             'Baad mein dobara koshish karein ya agency se seedha rabta karein.',
    'ur': 'ہمارا اسسٹنٹ اس وقت بند ہے، اس لیے آپ کے پیغام پر کوئی عمل نہیں ہوا۔ '
          'براہ کرم بعد میں دوبارہ کوشش کریں یا ایجنسی سے براہ راست رابطہ کریں۔',
    'ar': 'المساعد متوقف حالياً، لذلك لم يُنفَّذ أي إجراء على رسالتك. '
          'يرجى المحاولة لاحقاً أو التواصل مع الوكالة مباشرة.',
}


def paused_reply(message: str = '', language: str = '') -> str:
    """Deterministic, in the script the user wrote in."""
    from .lang import detect
    return _PAUSED.get(detect(message) if message.strip() else language, _PAUSED['en'])
