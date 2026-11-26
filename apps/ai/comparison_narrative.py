"""
Comparison narrative (T-4030, FR-CMP-003, ADR-067).

A short written comparison constrained to the comparison table: the model sees
the table as cells (``A.price``, ``B.bedrooms`` …) and must cite the cells each
sentence uses. Every sentence is re-checked here and left out when it cites no
cell or an unknown one, states a number that is not a cited cell's value (or the
difference of two cited cells of the same row), or says something the policy
does not allow (superlatives, value or investment judgements, advice, schools
or safety). Unknown cells are listed deterministically, not by the model.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from . import prompts as P

LETTERS = 'ABCD'
MAX_SENTENCES = 8
MAX_CHARS = 300
ROWS = (  # (row, label) shown to the model; values come from comparison.compare()
    ('price', 'asking price'), ('price_per_m2', 'asking price per m²'), ('size_m2', 'size m²'),
    ('bedrooms', 'bedrooms'), ('bathrooms', 'bathrooms'), ('floor', 'floor'), ('year_built', 'year built'),
    ('parking_spaces', 'parking spaces'), ('furnishing', 'furnishing'), ('completion', 'completion'),
    ('pets_allowed', 'pets allowed'), ('installments', 'installments'), ('location', 'location'),
    ('verification', 'verification'), ('listed_days', 'days listed'), ('deposit', 'deposit'),
    ('service_charge', 'yearly service charge'), ('amenities_only_here', 'amenities only this one has'),
    ('purchase_costs', 'estimated purchase fees'), ('annual_property_tax', 'estimated yearly property tax'),
)
_FORBIDDEN = re.compile(
    r'\b(best|perfect|ideal|unbeatable|bargain|steal|great (?:deal|value|buy)|good (?:deal|value|buy)|value for money'
    r'|under-?priced|over-?priced|worth|invest\w*|returns?|appreciat\w*|yield|safe\w*|secure|crime|school\w*'
    r'|you should|we recommend|i recommend|recommended|must[- ]see|don\'t miss|hurry)\b', re.I)
_NUM = re.compile(r'(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(crore|cr|lakh|lac|million|mn|m(?![²2\w])|k(?!\w))?', re.I)
_UNIT = {'crore': 10 ** 7, 'cr': 10 ** 7, 'lakh': 10 ** 5, 'lac': 10 ** 5, 'million': 10 ** 6, 'mn': 10 ** 6,
         'm': 10 ** 6, 'k': 10 ** 3}
TOLERANCE = Decimal('0.01')

PROMPT = P.register(P.Prompt('comparison.narrative', 1, """You write a short, neutral comparison of {count} property listings in {market_name}, in English, for a buyer or renter.

Rules:
- Use ONLY the TABLE cells. Each cell is written as LETTER.row: value. Do not add any fact, place, distance, view or feature.
- For every sentence list the cells it uses in "cells" (e.g. ["A.price", "B.price"]). A sentence that uses no cell must not be written.
- Numbers: write them as in the cells, or the difference between two cells of the same row. No other numbers, no percentages.
- Say plainly when a cell is "not specified" instead of guessing.
- Purchase fees and property tax are estimates; say so if you mention them.
- No superlatives, no judgement of value or price ("good deal", "overpriced", "worth"), nothing about investment, returns, schools, safety or crime, and no advice on which to choose.
- Refer to the listings as Listing A, Listing B, etc.
- 3 to 6 sentences.

Answer as JSON: {{"sentences": [{{"text": "...", "cells": ["A.price", "B.price"]}}]}}

TABLE:
{table}
"""))


def _value(item: dict, row: str):
    if row in ('purchase_costs', 'annual_property_tax'):
        rc = item.get('running_costs') or {}
        return rc.get(row)
    return item.get(row)


def _text(v) -> str:
    if v is None or v == [] or v == '':
        return 'not specified'
    if isinstance(v, dict):                                    # money
        basis = {'month': ' per month', 'year': ' per year', 'once': ' once'}.get(v.get('basis') or '', '')
        conv = f" (about {v['converted']:,} converted)" if v.get('converted') is not None else ''
        return f"{v['currency']} {v['amount']:,}{basis}{conv}"
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, list):
        return ', '.join(str(x) for x in v)
    if isinstance(v, float):
        return f'{v:g}'
    return str(v)


def cells(comparison: dict) -> dict[str, object]:
    out = {}
    for letter, item in zip(LETTERS, comparison['items']):
        for row, _ in ROWS:
            out[f'{letter}.{row}'] = _value(item, row)
    return out


def _numbers(v) -> list[Decimal]:
    if v is None or isinstance(v, bool):
        return []
    if isinstance(v, dict):
        return [Decimal(v[k]) for k in ('amount', 'converted') if v.get(k) is not None]
    if isinstance(v, (int, float)):
        return [Decimal(str(v))]
    found = []
    for raw, _unit in _NUM.findall(str(v)):
        try:
            found.append(Decimal(raw.replace(',', '')))
        except InvalidOperation:
            pass
    return found


def table(comparison: dict) -> str:
    lines = []
    for letter, item in zip(LETTERS, comparison['items']):
        lines.append(f"Listing {letter} ({item.get('purpose')}, {item.get('city')})")
        for row, label in ROWS:
            lines.append(f"{letter}.{row}: {_text(_value(item, row))}   [{label}]")
    return '\n'.join(lines)


def unknowns(comparison: dict) -> list[dict]:
    """Rows some listings specify and others do not (FR-CMP-003: unknowns flagged)."""
    out = []
    for row, label in ROWS:
        values = [(letter, _value(item, row)) for letter, item in zip(LETTERS, comparison['items'])]
        missing = [letter for letter, v in values if v in (None, '', [])]
        if missing and len(missing) < len(values) and row != 'amenities_only_here':
            out.append({'row': row, 'label': label, 'not_specified_for': missing})
    return out


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(Decimal('0.5'), abs(b) * TOLERANCE)


def check(text: str, cited: list, known: dict) -> list[str]:
    problems = []
    cited = [str(c) for c in (cited or []) if isinstance(c, str)]
    if not cited:
        problems.append('no_cell')
    if any(c not in known for c in cited):
        problems.append('unknown_cell')
    if _FORBIDDEN.search(text):
        problems.append('judgement_or_advice')
    by_row: dict[str, list[Decimal]] = {}
    for c in cited:
        if c in known:
            by_row.setdefault(c.split('.', 1)[1], []).extend(_numbers(known[c]))
    pool = [n for vals in by_row.values() for n in vals]
    pool += [abs(a - b) for vals in by_row.values() for i, a in enumerate(vals) for b in vals[i + 1:]]
    for raw, unit in _NUM.findall(text):
        try:
            n = Decimal(raw.replace(',', '')) * _UNIT.get((unit or '').lower(), 1)
        except InvalidOperation:
            continue
        if n <= 4 and n == n.to_integral_value() and not unit:     # "two of the four", "Listing 1"
            continue
        if not any(_close(n, a) for a in pool):
            problems.append('number_not_in_cells')
            break
    return problems


def narrative(comparison: dict, *, country: str = '', user=None, organization=None) -> dict:
    """Raises ``llm.LLMUnavailable`` (or ``AIStopped``) when no model can answer."""
    from . import llm
    known = cells(comparison)
    data = llm.complete_json('comparison.narrative', {'table': table(comparison),
                                                      'count': len(comparison['items'])},
                             country=country or None, organization=organization, user=user,
                             interaction_type='comparison_narrative', max_tokens=700, temperature=0.2)
    kept, left_out = [], []
    for item in ((data or {}).get('sentences') or [])[:MAX_SENTENCES]:
        if not isinstance(item, dict):
            continue
        text = ' '.join(str(item.get('text') or '').split())[:MAX_CHARS]
        if not text:
            continue
        problems = check(text, item.get('cells') or [], known)
        if problems:
            left_out.append({'problems': problems})
        else:
            kept.append({'text': text, 'cells': [c for c in item['cells'] if c in known]})
    letters = {letter: item['id'] for letter, item in zip(LETTERS, comparison['items'])}
    return {'sentences': kept, 'left_out': left_out, 'unknowns': unknowns(comparison), 'listings': letters,
            'label': 'Written from the comparison table only; check details with the lister.'}
