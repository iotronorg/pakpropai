"""
T-1120 — LLM router, prompt registry and market-pack fragments (ADR-028).

Providers are fakes: no network. Covers failover order, unconfigured skip,
circuit breaker, no failover after a tool ran, prompt provenance on
AIInteraction, market variables (no hard-coded Pakistan text for other
markets), pinned prompt digests, and the migrated call sites.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.ai import llm
from apps.ai import prompts as P
from apps.ai.backends.base import AIBackend, Completion, Step, ToolCall
from apps.ai.models import AIInteraction

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


class Fake(AIBackend):
    def __init__(self, name, reply='ok', fail=False, configured=True, run_tool=False):
        self.name, self.reply, self.fail, self.configured, self.run_tool = name, reply, fail, configured, run_tool
        self.calls = []

    def is_configured(self):
        return self.configured

    def complete(self, prompt, **kw):
        self.calls.append(prompt)
        if self.fail:
            raise ConnectionError('down')
        return Completion(self.reply, f'{self.name}-model', 11, 7)

    def step(self, system, messages, tools, **kw):
        self.calls.append(system)
        if self.run_tool and tools and messages[-1]['role'] != 'tool':
            return Step('', [ToolCall('c1', tools[0]['name'], {'x': 1})], f'{self.name}-model')
        if self.fail:
            raise TimeoutError('slow')
        return Step(self.reply, [], f'{self.name}-model', 3, 2)

    def analyze_image(self, image_bytes, mime_type, prompt):
        self.calls.append(prompt)
        if self.fail:
            raise ConnectionError('down')
        return self.reply

    def transcribe_audio(self, audio_bytes, mime_type):
        return ''

    @property
    def label(self):
        return f'{self.name}:m'


@override_settings(CACHES=LOCMEM, AI_PROVIDER_ORDER=['gemini', 'openai', 'local'],
                   AI_BREAKER_FAILURES=2, AI_BREAKER_SECONDS=60)
class RouterTest(TestCase):
    def setUp(self):
        cache.clear()
        self.fakes = {}

    def use(self, **fakes):
        self.fakes = fakes
        p = patch('apps.ai.llm.make_backend', side_effect=lambda n: self.fakes.get(n, Fake(n, configured=False)))
        p.start()
        self.addCleanup(p.stop)

    def test_failover_order_and_provenance(self):
        g, o = Fake('gemini', fail=True), Fake('openai', reply='{"risk": "low"}')
        self.use(gemini=g, openai=o)
        out = llm.complete_json('fraud.check', {'query': 'is plot 5 legit'}, country='AE',
                                interaction_type='fraud_check')
        self.assertEqual(out, {'risk': 'low'})
        self.assertEqual((len(g.calls), len(o.calls)), (1, 1))
        self.assertIn('United Arab Emirates', o.calls[0])
        row = AIInteraction.objects.get()
        prompt = P.get('fraud.check')
        self.assertEqual((row.provider, row.prompt_name, row.prompt_version, row.prompt_digest,
                          row.prompt_tokens, row.response_tokens),
                         ('openai', 'fraud.check', prompt.version, prompt.digest, 11, 7))

    def test_unconfigured_skipped_and_all_down(self):
        self.use(gemini=Fake('gemini', configured=False), local=Fake('local', fail=True))
        with self.assertRaises(llm.LLMUnavailable):
            llm.complete('fraud.check', {'query': 'x'})
        self.assertEqual(self.fakes['gemini'].calls, [])
        self.use()
        self.assertFalse(llm.any_configured())

    def test_breaker_moves_failing_provider_last(self):
        g, o = Fake('gemini', fail=True), Fake('openai')
        self.use(gemini=g, openai=o)
        for _ in range(2):
            llm.complete('fraud.check', {'query': 'x'})
        self.assertTrue(llm.breaker_open('gemini'))
        llm.complete('fraud.check', {'query': 'x'})
        self.assertEqual(len(g.calls), 2)                    # skipped while open
        g.fail = False
        o.fail = True
        llm.complete('fraud.check', {'query': 'x'})          # still tried as last resort
        self.assertFalse(llm.breaker_open('gemini'))         # success closes it

    def test_chat_no_failover_after_tool_side_effect(self):
        effects = []

        def list_property(x: int) -> dict:
            """Create a listing."""
            effects.append(x)
            return {}

        self.use(gemini=Fake('gemini', fail=True, run_tool=True), openai=Fake('openai', run_tool=True))
        from apps.ai.runtime import TurnCommitted
        with self.assertRaises(TurnCommitted):
            llm.chat('sell my house', [], [list_property], 'sys')
        self.assertEqual(effects, [1])                       # not repeated on openai
        self.use(gemini=Fake('gemini', fail=True), openai=Fake('openai', reply='hi'))
        self.assertEqual(llm.chat('hello', [], [list_property], 'sys').provider, 'openai')

    def test_response_cache(self):
        g = Fake('gemini', reply='cached answer')
        self.use(gemini=g)
        for _ in range(2):
            self.assertEqual(llm.complete('fraud.check', {'query': 'q'}, cache_ttl=60).text, 'cached answer')
        self.assertEqual(len(g.calls), 1)
        self.assertEqual(list(AIInteraction.objects.values_list('was_cached', flat=True).order_by('created_at')),
                         [False, True])

    def test_vision_and_call_sites(self):
        from apps.ai import scoring
        self.use(gemini=Fake('gemini', reply='{"fields": {}}'))
        self.assertEqual(scoring.ocr_document(b'img', 'image/png', country='GB'), '{"fields": {}}')
        self.assertIn('United Kingdom', self.fakes['gemini'].calls[0])
        self.fakes['gemini'].reply = '```json\n{"verdicts": [{"id": "a", "verdict": "Fair"}]}\n```'

        class R:
            source_id, title, city, location, price, currency, area_marla = 'a', 'Flat', 'Dubai', 'Marina', 1, 'AED', None
        self.assertEqual(scoring.batch_verdicts([R()], country='AE'), {'a': 'Fair'})
        self.fakes['gemini'].reply = 'no json here'
        with self.assertRaises(ValueError):
            scoring.fraud_check('x')


class PromptRegistryTest(TestCase):
    # Bump the prompt's version when you change its text, then update the digest here.
    PINNED = {
        'property.score': 2, 'fraud.check': 2, 'search.verdicts': 2, 'document.ocr': 2,
        'lead.summary': 1, 'lead.suggest_replies': 1, 'campaign.reengage.system': 1,
        'campaign.reengage.user': 1, 'agent.system': 1, 'agent.image': 1,
    }

    def test_library_complete_and_versioned(self):
        import apps.ai.knowledge  # noqa: F401 — registers agent.*
        names = {p.name: p for p in P.all_prompts()}
        for name, version in self.PINNED.items():
            self.assertEqual(names[name].version, version, name)
            self.assertRegex(names[name].digest, r'^[0-9a-f]{12}$')
        for cc in ('PK', 'AE', 'GB', 'US'):
            self.assertIn(f'market.{cc}.knowledge', names)

    def test_same_version_different_text_rejected_and_missing_var(self):
        P.register(P.Prompt('t.x', 1, 'a {v}'))
        P.register(P.Prompt('t.x', 1, 'a {v}'))              # idempotent re-import
        with self.assertRaises(P.PromptError):
            P.register(P.Prompt('t.x', 1, 'b {v}'))
        with self.assertRaises(P.PromptError):
            P.get('t.x').render()
        self.assertEqual(P.get('t.x').render(v=1).text, 'a 1')

    def test_market_neutral_templates(self):
        import apps.ai.knowledge  # noqa: F401
        for p in P.all_prompts():
            if p.name.startswith('market.'):
                continue
            for word in ('Pakistan', 'PKR', 'marla', 'CNIC', 'Urdu'):
                self.assertNotIn(word, p.template, f'{p.name} hard-codes {word}')

    def test_market_vars_and_fragments(self):
        from apps.ai.knowledge import get_market_knowledge
        self.assertIn('SECTION 7E', get_market_knowledge('PK'))
        self.assertNotIn('SECTION 7E', get_market_knowledge('AE'))
        self.assertEqual(get_market_knowledge('UK'), get_market_knowledge('GB'))
        v = P.market_vars('PK')
        self.assertEqual((v['currency'], v['area_unit']), ('PKR', 'marla'))
        r = llm.render('lead.summary', {'status': 'new', 'budget_min': 1, 'budget_max': 2, 'city': 'Leeds',
                                        'count': 0, 'transcript': ''}, country='GB')
        self.assertIn('GBP', r.text)
        self.assertEqual(r.ref['prompt_name'], 'lead.summary')
