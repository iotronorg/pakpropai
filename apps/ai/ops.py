"""
AI ops console (T-3090, GAP-103, ADR-061): what the operators of the assistant
need to see and steer, computed from what the runtime already records.

* prompts: the registry (name, version, digest, template) and the versions seen
  in use (``AIInteraction``);
* models: providers in order, whether configured, circuit-breaker state, the
  model each one uses and its current price (``ModelPrice``), recent usage;
* tools: the registry with flags and confirmation rules, recent calls by outcome;
* evals: runs of the deterministic suites, stored for comparison;
* traces and an overview of turns (``AITrace``, which holds no message text).

Kill switches and the cost report have their own endpoints (ADR-043, T-2011).
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from datetime import timezone as dt_tz

from django.conf import settings
from django.db import connection
from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone

MAX_DAYS = 90
MAX_FAILURES = 20


def window(first: str | None, last: str | None, default_days: int = 7) -> tuple[datetime, datetime, date, date]:
    """UTC days, inclusive. Raises ValueError with a message."""
    today = timezone.now().astimezone(dt_tz.utc).date()
    try:
        end = date.fromisoformat(last) if last else today
        start = date.fromisoformat(first) if first else end - timedelta(days=default_days - 1)
    except ValueError:
        raise ValueError('Dates must be YYYY-MM-DD.') from None
    if start > end:
        raise ValueError('"from" must not be after "to".')
    if (end - start).days + 1 > MAX_DAYS:
        raise ValueError(f'At most {MAX_DAYS} days.')
    return (datetime.combine(start, datetime.min.time(), dt_tz.utc),
            datetime.combine(end + timedelta(days=1), datetime.min.time(), dt_tz.utc), start, end)


# ── Prompts ───────────────────────────────────────────────────────────────────

def _load_prompt_modules():
    """Modules that register prompts on import (the registry fills as code is used)."""
    import importlib
    for name in ('apps.ai.faq', 'apps.ai.knowledge', 'apps.ai.listing_assistant', 'apps.ai.investment_commentary'):
        importlib.import_module(name)


def prompts(since: datetime) -> list[dict]:
    from . import prompts as P
    from .models import AIInteraction
    _load_prompt_modules()
    usage = {r['prompt_name']: r for r in AIInteraction.objects.filter(created_at__gte=since).exclude(prompt_name='')
             .values('prompt_name').annotate(calls=Count('id'), last_used=Max('created_at'))}
    return [{'name': p.name, 'version': p.version, 'digest': p.digest, 'description': p.description,
             'variables': sorted(p.variables), 'calls': usage.get(p.name, {}).get('calls', 0),
             'last_used': usage.get(p.name, {}).get('last_used')} for p in P.all_prompts()]


def prompt_detail(name: str) -> dict | None:
    from . import prompts as P
    from .models import AIInteraction
    _load_prompt_modules()
    try:
        p = P.get(name)
    except P.PromptError:
        return None
    seen = (AIInteraction.objects.filter(prompt_name=name).values('prompt_version', 'prompt_digest')
            .annotate(calls=Count('id'), first_used=Min('created_at'), last_used=Max('created_at'))
            .order_by('-prompt_version', '-last_used'))
    return {'name': p.name, 'version': p.version, 'digest': p.digest, 'description': p.description,
            'variables': sorted(p.variables), 'template': p.template,
            'versions_in_use': [{'version': r['prompt_version'], 'digest': r['prompt_digest'],
                                 'current': r['prompt_version'] == p.version and r['prompt_digest'] == p.digest,
                                 'calls': r['calls'], 'first_used': r['first_used'], 'last_used': r['last_used']}
                                for r in seen]}


# ── Models ────────────────────────────────────────────────────────────────────

def models(since: datetime) -> list[dict]:
    from .backends import make_backend
    from .llm import breaker_open, provider_order
    from .models import AITrace
    from .tracing import price_for
    today = timezone.now().date()
    usage = {r['provider']: r for r in AITrace.objects.filter(created_at__gte=since).exclude(provider='')
             .values('provider').annotate(turns=Count('id'), tokens_in=Sum('tokens_in', default=0),
                                          tokens_out=Sum('tokens_out', default=0),
                                          errors=Count('id', filter=~Q(error='')))}
    out = []
    for i, name in enumerate(provider_order(), 1):
        backend = make_backend(name)
        try:
            configured = bool(backend.is_configured())
        except Exception:  # noqa: BLE001 — a config lookup failure means "not usable"
            configured = False
        label = _label(backend)
        model = label.split(':', 1)[1] if ':' in label else label
        price = price_for(model, today) if model else None
        out.append({
            'provider': name, 'order': i, 'configured': configured, 'breaker_open': breaker_open(name),
            'model': model,
            'price': None if price is None else {
                'currency': price.currency, 'effective_from': price.effective_from,
                'input_per_mtok': price.input_per_mtok, 'cached_per_mtok': price.cached_per_mtok,
                'output_per_mtok': price.output_per_mtok},
            'usage': {k: usage.get(name, {}).get(k, 0) for k in ('turns', 'tokens_in', 'tokens_out', 'errors')},
        })
    return out


def _label(backend) -> str:
    try:
        return str(backend.label)
    except Exception:  # noqa: BLE001
        return ''


# ── Tools ─────────────────────────────────────────────────────────────────────

def tools(since: datetime) -> list[dict]:
    from apps.config.services import SystemConfigService

    from .tool_registry import REGISTRY
    features = SystemConfigService.get_features()
    with connection.cursor() as cur:
        cur.execute("SELECT t->>'name', COALESCE(t->>'status', ''), count(*) FROM ai_traces, "
                    "jsonb_array_elements(tool_calls) AS t WHERE created_at >= %s GROUP BY 1, 2", [since])
        calls = {}
        for name, status, n in cur.fetchall():
            calls.setdefault(name, {})[status or 'unknown'] = n
    out = []
    for spec in REGISTRY.values():
        try:
            params = sorted((spec.schema.get('parameters') or {}).get('properties') or {})
        except Exception:  # noqa: BLE001 — a tool whose schema cannot be built is still listed
            params = []
        out.append({'name': spec.name, 'side_effect': spec.side_effect,
                    'requires_confirmation': spec.requires_confirmation, 'flag': spec.flag or None,
                    'enabled': features.get(spec.flag, spec.flag_default) if spec.flag else True,
                    'markets': list(spec.markets), 'timeout_ms': spec.timeout_ms, 'parameters': params,
                    'calls': calls.get(spec.name, {})})
    return sorted(out, key=lambda t: t['name'])


# ── Evals ─────────────────────────────────────────────────────────────────────

def run_evals(names=None, *, user=None):
    from apps.core.audit import safe_record

    from .evals import offline
    from .models import AIEvalRun
    unknown = set(names or ()) - set(offline.SUITES)
    if unknown:
        raise ValueError(f'Unknown suites: {", ".join(sorted(unknown))}. Known: {", ".join(offline.SUITES)}.')
    started = time.monotonic()
    results = offline.run(list(names) if names else None)
    suites = []
    for r in results:
        d = r.as_dict()
        d['failures'] = d['failures'][:MAX_FAILURES]
        suites.append(d)
    failed = sum(len(r.failures) for r in results)
    run = AIEvalRun.objects.create(
        passed=all(r.ok for r in results), total=sum(r.total for r in results), failed=failed, suites=suites,
        started_by=str(user.pk) if user is not None else '', app_version=getattr(settings, 'APP_VERSION', ''),
        duration_ms=int((time.monotonic() - started) * 1000))
    safe_record('ai.eval_run', category='ai', actor=user, target=run,
                meta={'passed': run.passed, 'total': run.total, 'failed': run.failed,
                      'suites': [s['suite'] for s in suites]})
    return run


def eval_run(run, *, full: bool = False) -> dict:
    out = {'id': str(run.id), 'kind': run.kind, 'passed': run.passed, 'total': run.total, 'failed': run.failed,
           'app_version': run.app_version, 'duration_ms': run.duration_ms, 'created_at': run.created_at,
           'started_by': run.started_by or None,
           'suites': [{k: s[k] for k in ('suite', 'total', 'passed')} for s in run.suites]}
    if full:
        out['suites'] = run.suites
    return out


# ── Traces ────────────────────────────────────────────────────────────────────

TRACE_FIELDS = ('id', 'created_at', 'organization_id', 'conversation_ref', 'interactive', 'route', 'intent',
                'prompt_name', 'prompt_version', 'prompt_digest', 'provider', 'model', 'llm_calls', 'tokens_in',
                'tokens_out', 'cached_tokens', 'pricing', 'cost', 'currency', 'latency_ms', 'tool_calls',
                'guard_results', 'grounding_result', 'grounding_codes', 'citations', 'fallback_used', 'error')


def trace(t) -> dict:
    """Everything the trace holds except who the person was (no user, no message text exists)."""
    row = {f: getattr(t, f) for f in TRACE_FIELDS}
    row['id'] = str(t.id)
    row['organization_id'] = str(t.organization_id) if t.organization_id else None
    return row


def overview(qs) -> dict:
    from .models import AIKillSwitch
    agg = qs.aggregate(turns=Count('id'), model_turns=Count('id', filter=Q(llm_calls__gt=0)),
                       tokens_in=Sum('tokens_in', default=0), tokens_out=Sum('tokens_out', default=0),
                       fallback_turns=Count('id', filter=Q(fallback_used=True)),
                       failed_turns=Count('id', filter=~Q(error='')),
                       unpriced_turns=Count('id', filter=Q(pricing='unpriced')))

    def by(field):
        return {r[field] or '': r['n'] for r in qs.values(field).annotate(n=Count('id')).order_by('-n')[:20]}

    pct = qs.filter(llm_calls__gt=0).order_by().values('latency_ms')
    latency = _percentiles([v['latency_ms'] for v in pct[:50_000]])
    cost = [{'currency': r['currency'], 'total': r['total']} for r in qs.filter(pricing='priced')
            .values('currency').annotate(total=Sum('cost')).order_by('currency')]
    active = AIKillSwitch.objects.filter(released_at__isnull=True).values('id', 'scope', 'market_id',
                                                                         'organization_id', 'reason', 'created_at')
    return {**agg, 'by_route': by('route'), 'by_model': by('model'), 'by_prompt': by('prompt_name'),
            'grounding': by('grounding_result'), 'errors': by('error'), 'latency_ms': latency, 'cost': cost,
            'active_kill_switches': [{**k, 'id': str(k['id']),
                                      'organization_id': str(k['organization_id']) if k['organization_id'] else None}
                                     for k in active]}


def _percentiles(values: list[int]) -> dict:
    if not values:
        return {'p50': None, 'p95': None, 'max': None}
    values = sorted(values)

    def at(q):
        return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]
    return {'p50': at(0.5), 'p95': at(0.95), 'max': values[-1]}
