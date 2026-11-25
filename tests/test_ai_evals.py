"""T-2070 — AI eval suites: the deterministic gate, live scoring and the baseline rules."""
import json
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from apps.ai.backends.base import Step, ToolCall
from apps.ai.evals import live, offline


class OfflineGateTest(TestCase):
    """The same gate CI runs through ``manage.py run_ai_evals``."""

    def test_suites_pass_in_every_language(self):
        for result in offline.run():
            with self.subTest(result.name):
                self.assertEqual(result.failures, [])
                self.assertEqual(set(result.by_lang), set(offline.LANGUAGES))

    def test_a_failing_case_fails_the_suite(self):
        bad = offline.safety([{'id': 'x', 'lang': 'en', 'expect': 'block', 'text': 'a flat in Lahore'}])
        self.assertFalse(bad.ok)
        bad = offline.grounding([{'id': 'x', 'lang': 'en', 'expect': ['price'], 'user': '', 'listings': [],
                                  'reply': 'hello'}])
        self.assertFalse(bad.ok)
        self.assertFalse(offline.safety([]).ok)                  # an empty suite proves nothing

    def test_dataset_check(self):
        base = {'id': 'x', 'lang': 'en', 'market': 'PK', 'text': 't', 'tool': 'search_listings'}
        for args in ({'nope': 1}, {'purpose': 'steal'}, {'min_price': -5, 'max_price': 1}):
            self.assertFalse(offline.dataset([{**base, 'args': args}]).ok, args)
        self.assertFalse(offline.dataset([{**base, 'tool': 'gone', 'args': {}}]).ok)
        good = {**base, 'args': {'location': 'Lahore'}}
        self.assertTrue(offline.dataset([good]).ok)
        self.assertFalse(offline.dataset([good, good]).ok)

    def test_suites_have_no_side_effects(self):
        from apps.ai.guardrails import GuardrailEngine
        with patch.object(GuardrailEngine, '_run_side_effects') as effects, \
                patch('apps.ai.guardrails._write_security_trace') as trace:
            offline.run()
        effects.assert_not_called()
        trace.assert_not_called()

    def test_command(self):
        out = StringIO()
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / 'r.json'
            call_command('run_ai_evals', report=str(report), stdout=out)
            data = json.loads(report.read_text())
        self.assertIn('Eval gate passed', out.getvalue())
        self.assertEqual({s['suite'] for s in data['suites']}, set(offline.SUITES))
        failing = offline.SuiteResult('safety')
        failing.add({'id': 'x', 'lang': 'en'}, False, 'block', 'allow')
        with patch('apps.ai.evals.offline.run', return_value=[failing]), \
                self.assertRaisesMessage(CommandError, 'Eval gate failed'):
            call_command('run_ai_evals', stdout=StringIO())


def call(name, **args):
    return ToolCall('c', name, args)


CASE = {'id': 'c1', 'lang': 'en', 'market': 'PK', 'text': 't', 'tool': 'search_listings',
        'args': {'location': 'Gulberg, Lahore', 'purpose': 'buy', 'max_price': 20_000_000}}


class ScoringTest(SimpleTestCase):
    def test_exact_and_equivalent_answers(self):
        row = live.score_case(CASE, [call('search_listings', location='lahore gulberg', purpose='sale',
                                          max_price='20,000,000')])
        self.assertEqual((row['tp'], row['fp'], row['fn'], row['money'], row['tool_ok']), (3, 0, 0, True, True))

    def test_wrong_missing_and_invented_fields(self):
        row = live.score_case(CASE, [call('search_listings', location='Gulberg Lahore', max_price=2_000_000,
                                          min_bedrooms=3)])
        self.assertEqual((row['tp'], row['fp'], row['fn'], row['money']), (1, 2, 2, False))
        self.assertEqual(row['wrong'], ['bedrooms.min', 'price.max', 'purpose'])

    def test_invented_currency_is_a_money_error(self):
        row = live.score_case(CASE, [call('search_listings', location='Gulberg Lahore', purpose='buy',
                                          max_price=20_000_000, currency='USD')])
        self.assertFalse(row['money'])

    def test_tool_choice_and_validity(self):
        row = live.score_case(CASE, [call('nope')])
        self.assertEqual((row['tool_ok'], row['valid'], row['fn']), (False, 0, 3))
        row = live.score_case(CASE, [call('search_listings', min_price='abc')])
        self.assertEqual((row['tool_ok'], row['valid']), (True, 0))
        quiet = {**CASE, 'tool': '', 'args': {}}
        self.assertTrue(live.score_case(quiet, [])['tool_ok'])
        self.assertFalse(live.score_case(quiet, [call('connect_to_agent', city='Lahore')])['tool_ok'])
        self.assertFalse(live.score_case(CASE, [call('search_listings', location='Lahore'),
                                                call('connect_to_agent', city='Lahore')])['tool_ok'])

    def test_gate(self):
        good = {'f1': .9, 'money_accuracy': .9, 'tool_accuracy': .9, 'valid_args': 1.0, 'errors': 0, 'cases': 5}
        summary = {lang: dict(good) for lang in offline.LANGUAGES}
        base = {'languages': {lang: dict(good) for lang in offline.LANGUAGES}}
        self.assertEqual(live.gate(summary, base), [])
        self.assertEqual(len(live.gate(summary, None)), 3)
        for change in ({'f1': .87}, {'money_accuracy': .87}, {'tool_accuracy': .5}, {'valid_args': .94},
                       {'valid_args': None}, {'errors': 1}, {'f1': None}):
            self.assertEqual(len(live.gate({**summary, 'ur': {**good, **change}}, base)), 1, change)
        self.assertEqual(live.gate({**summary, 'ur': {**good, 'f1': .885}}, base), [])
        self.assertEqual(len(live.gate({k: v for k, v in summary.items() if k != 'roman'}, base)), 1)


class Perfect:
    """A provider that returns the golden answer."""
    name = 'fake'

    def __init__(self, fail=()):
        self.by_text = {c['text']: c for c in offline.load('extraction')}
        self.fail, self.seen = set(fail), []

    def is_configured(self):
        return True

    def step(self, system, messages, tools, **kw):
        case = self.by_text[messages[-1]['text']]
        self.seen.append((system, [t['name'] for t in tools]))
        if case['id'] in self.fail:
            raise RuntimeError('quota')
        args = {'description': 'advance demanded'} if case['tool'] == 'run_fraud_check' else case['args']
        calls = [ToolCall('c', case['tool'], args)] if case['tool'] else []
        return Step('' if calls else 'ok', calls, 'm', 10, 2)


class LiveRunTest(TestCase):
    def run_cmd(self, backend, **kw):
        out = StringIO()
        with patch('apps.ai.management.commands.run_ai_evals.make_backend', return_value=backend):
            call_command('run_ai_evals', live=True, suite=['dataset'], stdout=out, **kw)
        return out.getvalue()

    def test_perfect_model_scores_one_and_nothing_is_executed(self):
        backend = Perfect()
        with patch('apps.ai.runtime._execute') as execute:
            result = live.run(backend)
        execute.assert_not_called()
        for lang, s in result['languages'].items():
            self.assertEqual((s['f1'], s['money_accuracy'], s['errors']), (1.0, 1.0, 0), lang)
        self.assertEqual(set(result['below_target_size']), set(offline.LANGUAGES))   # seed set only
        system, tools = backend.seen[0]
        self.assertIn('RealTron AI', system)
        self.assertIn('search_listings', tools)
        self.assertNotIn('initiate_deal_lock', tools)            # frozen, as in production

    def test_baseline_rules(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'b.json')
            with self.assertRaisesMessage(CommandError, 'no baseline'):
                self.run_cmd(Perfect(), baseline=path)
            with self.assertRaisesMessage(CommandError, 'provider errors'):
                self.run_cmd(Perfect(fail=['ur-01']), baseline=path, write_baseline=True)
            self.assertFalse(Path(path).exists())                # an incomplete run is never a baseline
            self.run_cmd(Perfect(), baseline=path, write_baseline=True)
            self.assertIn('Eval gate passed', self.run_cmd(Perfect(), baseline=path))
            stale = json.loads(Path(path).read_text())
            stale['fingerprint']['prompt_digest'] = 'old'
            Path(path).write_text(json.dumps(stale))
            out = StringIO()
            call_command('run_ai_evals', baseline=path, suite=['dataset'], stdout=out)
            self.assertIn('run --live before release', out.getvalue())

    def test_guards(self):
        with self.assertRaisesMessage(CommandError, 'needs --live'):
            call_command('run_ai_evals', write_baseline=True, stdout=StringIO())
        with self.assertRaisesMessage(CommandError, 'not configured'):
            self.run_cmd(SimpleNamespace(is_configured=lambda: False))
