"""
Deterministic eval suites (T-2070, ADR-043): no model, no network, no writes.

They test the layers that must hold whatever the model says: the input guard,
the grounding checker and the fairness policy, in English, Urdu and Roman Urdu.
``dataset`` checks that the golden set for the live suite is itself valid.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).parent / 'data'
LANGUAGES = ('en', 'ur', 'roman')


@dataclass
class SuiteResult:
    name: str
    total: int = 0
    failures: list[dict] = field(default_factory=list)      # {id, lang, expected, got}
    by_lang: dict = field(default_factory=dict)             # lang -> [passed, total]

    def add(self, case: dict, ok: bool, expected, got) -> None:
        self.total += 1
        row = self.by_lang.setdefault(case.get('lang', 'en'), [0, 0])
        row[1] += 1
        if ok:
            row[0] += 1
        else:
            self.failures.append({'id': case['id'], 'lang': case.get('lang'), 'expected': expected, 'got': got})

    @property
    def ok(self) -> bool:
        return self.total > 0 and not self.failures

    def as_dict(self) -> dict:
        return {'suite': self.name, 'total': self.total, 'passed': self.total - len(self.failures),
                'by_language': {k: {'passed': p, 'total': t} for k, (p, t) in sorted(self.by_lang.items())},
                'failures': self.failures}


def load(name: str) -> list[dict]:
    with (DATA / f'{name}.jsonl').open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def safety(cases=None) -> SuiteResult:
    """Known attacks are blocked; ordinary messages (including look-alikes) are not."""
    from apps.ai.guardrails import GuardrailEngine, Severity
    out = SuiteResult('safety')
    for c in cases if cases is not None else load('safety'):
        violation, _, severity = GuardrailEngine.inspect(c['text'])
        blocked = severity is not None and severity >= Severity.HIGH
        ok = blocked if c['expect'] == 'block' else not violation
        out.add(c, ok, c['expect'], violation or 'allow')
    return out


def fairness(cases=None) -> SuiteResult:
    """Subjective and protected wording never becomes a filter; ordinary wording is left alone."""
    from apps.properties.criteria import unsupported
    out = SuiteResult('fairness')
    for c in cases if cases is not None else load('fairness'):
        got = sorted({u.reason for u in unsupported(c['text'])})
        out.add(c, got == sorted(c['expect']), c['expect'], got)
    return out


def _listing(row: dict):
    from apps.ai.grounding import Listing
    from apps.properties.models import Property
    value, unit = row['size']
    return Listing(id=row['id'], title=row['title'], city=row['city'], location=row['location'], price=row['price'],
                   currency=row['currency'], size_m2=float(value) * float(Property._SQM_FACTORS[unit]),
                   size_label=f'{value} {unit}', bedrooms=row['bedrooms'], bathrooms=row['bathrooms'],
                   amenities=set(row['amenities']), installments=row['installments'], verified=row['verified'])


def grounding(cases=None) -> SuiteResult:
    """Every false listing fact is caught (the expected code is among those found); true replies pass."""
    from apps.ai import grounding as G
    out = SuiteResult('grounding')
    for c in cases if cases is not None else load('grounding'):
        evidence = G.Evidence(c['user'], [], '')
        evidence._listings = [_listing(r) for r in c['listings']]      # sources given, so no database or cache
        got = G.check(c['reply'], evidence).codes
        ok = set(c['expect']) <= set(got) if c['expect'] else not got
        out.add(c, ok, c['expect'], got)
    return out


def dataset(cases=None) -> SuiteResult:
    """Golden extraction cases: unique ids, known tools, arguments the tool and SearchCriteria accept."""
    from apps.ai import tool_registry as R
    from apps.properties.criteria import CriteriaInvalid

    from . import live
    out, seen = SuiteResult('dataset'), set()
    for c in cases if cases is not None else load('extraction'):
        problem = ''
        if c['id'] in seen:
            problem = 'duplicate id'
        seen.add(c['id'])
        if c['tool'] and c['tool'] not in R.REGISTRY:
            problem = 'unknown tool'
        elif unknown := set(c['args']) - set(R.REGISTRY[c['tool']].schema['parameters']['properties'] if c['tool'] else ()):
            problem = f'unknown arguments {sorted(unknown)}'
        elif c['tool'] == live.SEARCH:
            try:
                fields = live.fields_of(c['args'])
                if len(fields) < len(c['args']) - ('size_unit' in c['args']):
                    problem = 'an argument has no effect on the criteria'
            except CriteriaInvalid as exc:
                problem = str(exc.errors)
        out.add(c, not problem, 'valid', problem or 'valid')
    return out


SUITES = {f.__name__: f for f in (safety, grounding, fairness, dataset)}


def run(names=None) -> list[SuiteResult]:
    return [SUITES[n]() for n in names or SUITES]
