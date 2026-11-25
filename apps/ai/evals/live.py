"""
Live eval: criteria extraction and tool choice against a real model (T-2070).

One model call per case, with the production system prompt, market knowledge and
tool schemas. Proposed calls are scored and never executed. Costs money: run on
demand only. The kill switch does not apply, because a fix must be evaluated
before the assistant is restarted.

Scores per language: field F1 over the normalised ``SearchCriteria``, money
accuracy (amount, currency and period), tool accuracy and valid arguments.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from . import offline

SEARCH = 'search_listings'
TARGET_PER_LANGUAGE = 300           # AI spec §9
MIN_VALID_ARGS = 0.95               # AI spec §9
MAX_DROP = 0.02                     # allowed F1 regression
BASELINE = Path(__file__).parent / 'baseline.json'
_MONEY = ('price.min', 'price.max', 'price.currency', 'price.period')


# ── Scoring ───────────────────────────────────────────────────────────────────

def _flatten(value, prefix='') -> dict:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(_flatten(v, f'{prefix}.{k}' if prefix else k))
        return out
    return {prefix: value}


def _norm(key, value):
    if key == 'location.free_text':                    # word order and commas do not matter
        return tuple(sorted(''.join(ch if ch.isalnum() else ' ' for ch in value.lower()).split()))
    if isinstance(value, list):
        return tuple(sorted(value))
    return value


def fields_of(args: dict) -> dict:
    """Search arguments → the flat criteria fields they set. Raises CriteriaInvalid."""
    from apps.ai import tool_registry as R
    from apps.ai._tools_listings import _patch
    from apps.properties import criteria as C
    defaults = {n: ('' if p['type'] == 'string' else False if p['type'] == 'boolean' else 0)
                for n, p in R.REGISTRY[SEARCH].schema['parameters']['properties'].items()}
    raw = _patch({}, {**defaults, **args})
    dumped = C.parse(raw).model_dump(mode='json', exclude_defaults=True)
    flat = {k: _norm(k, v) for k, v in _flatten(dumped).items()}
    flat.pop('category', None) if 'subtypes' in flat else None      # implied by the subtype
    return flat


def score_case(case: dict, calls: list) -> dict:
    """``calls``: what the model proposed (``name``, ``args``)."""
    from apps.ai import tool_registry as R
    from apps.properties.criteria import CriteriaInvalid
    row = {'id': case['id'], 'lang': case['lang'], 'tool_ok': False, 'calls': len(calls), 'valid': 0,
           'tp': 0, 'fp': 0, 'fn': 0, 'money': None, 'wrong': []}
    valid = []
    for call in calls:
        spec = R.REGISTRY.get(call.name)
        try:
            args = R.validate_args(spec, call.args) if spec is not None else None
            if args is not None and call.name == SEARCH:
                fields_of(args)
        except (R.ToolArgsInvalid, CriteriaInvalid):
            args = None
        if args is not None:
            row['valid'] += 1
            valid.append((call.name, args))
    names = [c.name for c in calls]
    row['tool_ok'] = names == ([case['tool']] if case['tool'] else [])
    if case['tool'] == SEARCH:
        want = fields_of(case['args'])
        got = next((fields_of(a) for n, a in valid if n == SEARCH), {})
        row['tp'] = sum(got.get(k) == v for k, v in want.items())
        row['fn'], row['fp'] = len(want) - row['tp'], sum(want.get(k) != v for k, v in got.items())
        row['wrong'] = sorted({k for k in {*want, *got} if want.get(k) != got.get(k)})
        if any(k in want for k in _MONEY):
            row['money'] = all(want.get(k) == got.get(k) for k in _MONEY)
    return row


def _f1(tp, fp, fn):
    return round(2 * tp / (2 * tp + fp + fn), 4) if tp or fp or fn else None


def summarise(rows: list[dict]) -> dict:
    out = {}
    for lang in sorted({r['lang'] for r in rows}):
        mine = [r for r in rows if r['lang'] == lang]
        total, calls = Counter(), sum(r['calls'] for r in mine)
        for r in mine:
            total.update({k: r[k] for k in ('tp', 'fp', 'fn')})
        money = [r['money'] for r in mine if r['money'] is not None]
        out[lang] = {
            'cases': len(mine), 'f1': _f1(total['tp'], total['fp'], total['fn']),
            'money_accuracy': round(sum(money) / len(money), 4) if money else None,
            'tool_accuracy': round(sum(r['tool_ok'] for r in mine) / len(mine), 4),
            'valid_args': round(sum(r['valid'] for r in mine) / calls, 4) if calls else None,
            'errors': sum(bool(r.get('error')) for r in mine),
        }
    return out


# ── Gate ──────────────────────────────────────────────────────────────────────

def gate(summary: dict, baseline: dict | None) -> list[str]:
    """Reasons the run fails; [] passes. Without a baseline the run cannot pass."""
    problems = []
    for lang in offline.LANGUAGES:
        s = summary.get(lang)
        if s is None:
            problems.append(f'{lang}: no cases')
            continue
        if s['errors']:
            problems.append(f'{lang}: {s["errors"]} provider errors, the run is incomplete')
        if s['valid_args'] is None or s['valid_args'] < MIN_VALID_ARGS:
            problems.append(f'{lang}: valid arguments {s["valid_args"]} < {MIN_VALID_ARGS}')
        base = ((baseline or {}).get('languages') or {}).get(lang)
        if base is None:
            problems.append(f'{lang}: no baseline (record one with --write-baseline after review)')
            continue
        for metric in ('f1', 'money_accuracy', 'tool_accuracy'):
            if (s[metric] or 0) < (base.get(metric) or 0) - MAX_DROP:
                problems.append(f'{lang}: {metric} {s[metric]} fell from {base[metric]}')
    return problems


def read_baseline(path: Path = BASELINE) -> dict | None:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


# ── Run ───────────────────────────────────────────────────────────────────────

def _specs(market: str):
    from apps.ai.tool_registry import enabled_tools
    return enabled_tools(features={}, market=market)     # the product defaults, not this database's settings


def fingerprint() -> dict:
    """What the scores belong to: the prompts, the tool schemas and the cases."""
    from apps.ai.knowledge import AGENT_SYSTEM
    from apps.ai.prompts import market_fragment
    cases = offline.load('extraction')
    markets = sorted({c['market'] for c in cases})

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return {'prompt_version': AGENT_SYSTEM.version, 'prompt_digest': AGENT_SYSTEM.digest,
            'knowledge_digest': digest([getattr(market_fragment(m), 'text', '') for m in markets]),
            'tools_digest': digest([s.schema for m in markets for s in _specs(m)]), 'dataset_digest': digest(cases)}


def run(backend, cases=None, timeout: float = 30.0) -> dict:
    from apps.ai.knowledge import AGENT_SYSTEM
    from apps.ai.prompts import market_fragment
    rows, model, tokens = [], '', [0, 0]
    for case in cases if cases is not None else offline.load('extraction'):
        fragment = market_fragment(case['market'])
        system = AGENT_SYSTEM.render().text + (f'\n\n{fragment.text}' if fragment else '')
        try:
            step = backend.step(system, [{'role': 'user', 'text': case['text']}],
                                [s.schema for s in _specs(case['market'])], timeout=timeout)
        except Exception as exc:
            rows.append({**score_case(case, []), 'tool_ok': False, 'error': type(exc).__name__})
            continue
        model = step.model or model
        tokens = [tokens[0] + step.prompt_tokens, tokens[1] + step.completion_tokens]
        rows.append(score_case(case, step.tool_calls))
    summary = summarise(rows)
    return {'provider': getattr(backend, 'name', ''), 'model': model, 'fingerprint': fingerprint(),
            'tokens_in': tokens[0], 'tokens_out': tokens[1], 'languages': summary,
            'below_target_size': [k for k, s in summary.items() if s['cases'] < TARGET_PER_LANGUAGE],
            'failures': [r for r in rows if not r['tool_ok'] or r['wrong'] or r.get('error')]}
