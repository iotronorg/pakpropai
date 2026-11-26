"""
Commentary on an investment calculation (T-4010, FR-INV-003, ADR-065).

The model explains the calculator's result in plain words; it never computes.
Each paragraph is re-checked here and left out when it states a number the
result does not show, or says something the policy does not allow (advice to
buy or sell, promises of returns or growth, "safe"/"guaranteed"). The text is
always labelled general information, not financial advice, and is kept apart
from the numbers.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from . import prompts as P

LABEL = 'General information, not financial advice.'
MAX_PARAGRAPHS = 5
MAX_CHARS = 600
_NUMBER = re.compile(r'(?<![\w.])\d[\d,]*(?:\.\d+)?')
_FORBIDDEN = re.compile(
    r'\b(guarantee\w*|risk[- ]free|safe (?:bet|investment)|sure (?:thing|bet)|will (?:rise|increase|grow|appreciate|go up|double)'
    r'|you should (?:buy|sell|invest)|we recommend|i recommend|recommended (?:buy|purchase)|must buy|can\'t lose|cannot lose'
    r'|best investment|great investment|no[- ]brainer)\b', re.I)

PROMPT = P.register(P.Prompt('investment.commentary', 1, """You explain an investment calculation for a property in {market_name}, in plain English, for a non-expert.

Rules:
- Explain only what the RESULT below shows: what the yields, cash flow and scenarios mean, which assumptions drive them, and which assumptions the reader should check.
- Quote numbers exactly as written in the RESULT (same digits, no rounding, no new numbers, no conversions). Write no number that is not there.
- Do not advise buying or selling. Do not predict prices, rents or growth. Never say an investment is safe, guaranteed or certain.
- Mention when a figure rests on a platform default or on asking rents of other listings, and that taxes and fees are estimates.
- 2 to 4 short paragraphs.

Answer as JSON: {{"paragraphs": ["...", "..."]}}

RESULT:
{result}
"""))


def _lines(result: dict) -> str:
    out = [f"currency: {result['currency']}", f"price: {result['price']}"]
    out += [f"{f['name']}: {f['value']}{'%' if f['unit'] == 'pct' else ''}  ({f['formula']})" for f in result['figures']]
    for a in result['assumptions']:
        extra = ''
        if a['source'] == 'platform_median':
            d = a['detail']
            extra = f" (median of {d['n']} rent listings, middle half {d['p25']} to {d['p75']})"
        out.append(f"assumption {a['name']}: {a['value']} [source: {a['source']}]{extra}")
    for s in result['scenarios']:
        out.append(f"scenario: appreciation {s['appreciation_pct']}% a year for {s['holding_years']} years -> "
                   f"value_at_end {s['value_at_end']}, total_return {s['total_return']}, roi {s['roi_pct']}%")
    out += [f"note: {n}" for n in result['notes']]
    return '\n'.join(out)


def _allowed(result: dict) -> list[Decimal]:
    from apps.properties.investment import numbers
    vals = []
    for raw in numbers(result):
        try:
            vals.append(Decimal(raw))
        except InvalidOperation:
            pass
    return vals


def unsupported_numbers(text: str, allowed: list[Decimal]) -> list[str]:
    bad = []
    for raw in _NUMBER.findall(text):
        try:
            n = Decimal(raw.replace(',', ''))
        except InvalidOperation:
            bad.append(raw)
            continue
        if n <= 12 and n == n.to_integral_value():       # "2 to 4", "12 months", "one of 3 rates"
            continue
        if not any(abs(n - a) <= max(Decimal('0.05'), abs(a) * Decimal('0.001')) for a in allowed):
            bad.append(raw)
    return bad


def check(paragraph: str, allowed: list[Decimal]) -> list[str]:
    problems = []
    if _FORBIDDEN.search(paragraph):
        problems.append('advice_or_promise')
    if unsupported_numbers(paragraph, allowed):
        problems.append('number_not_in_result')
    return problems


def commentary(result: dict, *, user=None, organization=None) -> dict:
    """Raises ``llm.LLMUnavailable`` (or ``AIStopped``) when no model can answer."""
    from . import llm
    data = llm.complete_json('investment.commentary', {'result': _lines(result)}, country=result['country'],
                             organization=organization, user=user, interaction_type='investment_commentary',
                             max_tokens=700, temperature=0.2)
    allowed = _allowed(result)
    kept, dropped = [], []
    for raw in ((data or {}).get('paragraphs') or [])[:MAX_PARAGRAPHS]:
        text = ' '.join(str(raw or '').split())[:MAX_CHARS]
        if not text:
            continue
        problems = check(text, allowed)
        (dropped if problems else kept).append({'text': text, **({'problems': problems} if problems else {})})
    return {'label': LABEL, 'paragraphs': [k['text'] for k in kept],
            'left_out': [{'problems': d['problems']} for d in dropped]}
