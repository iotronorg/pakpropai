"""
Per-turn AI traces and cost (T-2011, ADR-031, AI spec §10/§11).

``AIServiceManager.process`` opens a trace; the layers below add to it with
:func:`note`; :func:`save` prices the turn from the provider-reported usage of
every model call and writes one ``AITrace``. Tracing never breaks a reply.
"""
from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

MTOK = Decimal(1_000_000)


@dataclass
class Trace:
    started: float = field(default_factory=time.monotonic)
    values: dict = field(default_factory=dict)
    guards: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)      # for the conversation store, not AITrace columns


_current: contextvars.ContextVar[Trace | None] = contextvars.ContextVar('ai_trace', default=None)


def begin() -> Trace:
    trace = Trace()
    _current.set(trace)
    return trace


def note(**values) -> None:
    trace = _current.get()
    if trace is not None:
        trace.values.update(values)


def noted(key: str):
    trace = _current.get()
    return trace.values.get(key) if trace is not None else None


def note_extra(**values) -> None:
    trace = _current.get()
    if trace is not None:
        trace.extra.update(values)


def note_guard(stage: str, reason: str) -> None:
    trace = _current.get()
    if trace is not None:
        trace.guards.append({'stage': stage, 'reason': str(reason or '')[:80]})


def note_llm(result) -> None:
    """Provider, prompt and tool calls of a finished ``llm.chat`` turn (arguments dropped)."""
    keep = ('name', 'step', 'status', 'reason', 'side_effect', 'ms', 'pending_action')
    note(provider=result.provider, model=result.model,
         grounding_result=result.grounding, grounding_codes=list(result.grounding_codes or ()),
         citations=list(result.citations or ())[:20],
         tool_calls=[{k: c[k] for k in keep if k in c} for c in result.tool_calls or ()],
         **(result.prompt.ref if result.prompt else {}))
    if result.results is not None:
        note_extra(results=result.results, criteria=result.criteria, matches=result.matches)


def price_for(model: str, on):
    from .models import ModelPrice
    return ModelPrice.objects.filter(model=model, effective_from__lte=on).order_by('-effective_from').first()


def call_cost(price, tokens_in: int, tokens_out: int, cached: int = 0) -> Decimal:
    """Cached tokens are part of ``tokens_in`` (Gemini and OpenAI both report them that way)."""
    cached = min(cached, tokens_in)
    cached_rate = price.input_per_mtok if price.cached_per_mtok is None else price.cached_per_mtok
    return ((tokens_in - cached) * price.input_per_mtok + cached * cached_rate
            + tokens_out * price.output_per_mtok) / MTOK


def cost_of(records, on=None):
    """``(pricing, cost, currency)``. One unreported or unpriced call → no cost for the turn."""
    from .models import AITrace
    if not records:
        return AITrace.Pricing.NO_CALLS, None, ''
    on = on or timezone.now().date()
    prices, total, currencies = {}, Decimal(0), set()
    for model, tokens_in, tokens_out, cached, reported in records:
        if model not in prices:
            prices[model] = price_for(model, on)
        if not reported or prices[model] is None:
            return AITrace.Pricing.UNPRICED, None, ''
        currencies.add(prices[model].currency)
        total += call_cost(prices[model], tokens_in, tokens_out, cached)
    if len(currencies) != 1:
        return AITrace.Pricing.UNPRICED, None, ''
    return AITrace.Pricing.PRICED, total.quantize(Decimal('1e-8')), currencies.pop()


def save(trace: Trace, *, phone: str, user=None, organization=None, interactive: bool = True):
    """The stored AITrace, or None when it could not be written."""
    from apps.core.crypto import blind_index

    from .confirmation import conversation_key
    from .models import AITrace
    from .usage import current_usage
    _current.set(None)
    try:
        with transaction.atomic():
            usage = current_usage()
            pricing, cost, currency = cost_of(usage.records)
            values = dict(trace.values)
            values['model'] = (values.get('model') or usage.model)[:80]
            return AITrace.objects.create(
                organization=organization if getattr(organization, 'pk', None) else None,
                user=user if getattr(user, 'pk', None) else None,
                conversation_ref=blind_index(conversation_key(phone, organization)),
                interactive=interactive, llm_calls=usage.calls, tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out, cached_tokens=usage.cached_tokens,
                pricing=pricing, cost=cost, currency=currency,
                latency_ms=int((time.monotonic() - trace.started) * 1000),
                guard_results=trace.guards, **values)
    except Exception as exc:
        logger.error('AITrace write failed: %s', exc, exc_info=True)
        return None


def purge() -> int:
    from datetime import timedelta

    from django.conf import settings

    from .models import AITrace
    cutoff = timezone.now() - timedelta(days=settings.AI_TRACE_RETENTION_DAYS)
    return AITrace.objects.filter(created_at__lt=cutoff).delete()[0]
