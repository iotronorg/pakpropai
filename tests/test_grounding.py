"""T-2013 — grounding checker v2 (ADR-032, MVP acceptance 7); KI-060 AML session block."""
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.ai import grounding as G
from apps.ai import runtime
from apps.ai import tool_registry as R
from apps.ai._tools_context import set_context
from apps.ai.backends.base import Step
from apps.audit.models import ComplianceViolation
from apps.properties.models import Amenity, Property
from tests.factories import make_client, make_org, make_property
from tests.test_runtime import LOCMEM, Scripted, calls, final

PHONE = '923001237700'


class ExtractionTest(TestCase):
    def test_money(self):
        for text, want in (
                ('PKR 25,000,000', [(25_000_000, 'PKR')]), ('Rs. 2.5 crore', [(25_000_000, 'PKR')]),
                ('PKR 150 lakh', [(15_000_000, 'PKR')]), ('2 crore 50 lakh', [(25_000_000, '')]),
                ('AED 1.2 million', [(1_200_000, 'AED')]), ('$450k', [(450_000, 'USD')]),
                ('500,000 AED', [(500_000, 'AED')]), ('PKR25000000', [(25_000_000, 'PKR')]),
                ('۲ کروڑ', [(20_000_000, '')]), ('offers 2500 and year 2026', []),
                ('10 marla, 1,200 sqft, 5%', []), ('call 03001234567', [])):
            self.assertEqual(G.money(text), [(Decimal(a), c) for a, c in want], text)

    def test_sizes(self):
        for text, want in (('1 kanal', 505.857), ('10 Marla', 252.929), ('10M House', 252.929),
                           ('1,200 sq ft', 111.48), ('100 sqm', 100), ('100 m²', 100)):
            self.assertAlmostEqual(G.sizes_m2(text)[0], want, 1, text)
        self.assertEqual(G.sizes_m2('5M people? no: 2 months, 3 meetings'), [G.sizes_m2('5M')[0]])


@override_settings(CACHES=LOCMEM)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.org = make_org(name='G', operational_mode='production')
        self.house = make_property(org=self.org, title='Corner House Gulberg', location='Gulberg', price=25_000_000,
                                   size_value=10, size_unit='marla', bedrooms=4, bathrooms=5, is_sandbox=False)
        self.flat = make_property(org=self.org, title='Sky View Flat', location='Bahria Town', price=9_000_000,
                                  size_value=5, size_unit='marla', bedrooms=2, is_sandbox=False)
        self.house.amenities.add(self.amenity('gym', 'Gym'))
        self.amenity('swimming_pool', 'Swimming pool')
        set_context(make_client(phone='+' + PHONE), PHONE)

    def amenity(self, key, label):
        return Amenity.objects.get_or_create(key=key, defaults={'group': 'building', 'labels': {'en': label}})[0]

    def rows(self, *props):
        return {'properties': [{'id': str(p.pk), 'source': 'pakprop', 'title': p.title} for p in props]}

    def evidence(self, *props, message='show me houses', system=''):
        e = G.Evidence(message, [], system)
        if props:
            e.add(self.rows(*props))
        return e

    def codes(self, reply, *props, **kw):
        return G.check(reply, self.evidence(*(props or (self.house, self.flat)), **kw)).codes


class CheckTest(Base):
    def test_faithful_reply_passes_and_cites(self):
        reply = ('I found 2 options:\n\n1. *Corner House Gulberg*\n   📍 Gulberg, Lahore\n   PKR 2.5 crore · 10 marla · '
                 '4 bed · 5 bath · gym\n\n2. *Sky View Flat* — 90 lakh, 5 marla, 2 bedrooms')
        v = G.check(reply, self.evidence(self.house, self.flat))
        self.assertEqual((v.violations, v.cited), ([], [str(self.house.pk), str(self.flat.pk)]))

    def test_wrong_facts(self):
        for reply, code in (
                ('Corner House Gulberg is PKR 2.1 crore', 'price'),
                ('Corner House Gulberg is AED 25,000,000', 'currency'),
                ('Corner House Gulberg is 1 kanal', 'size'),
                ('Corner House Gulberg has 6 bedrooms', 'bedrooms'),
                ('Sky View Flat has 3 bathrooms', 'bathrooms'),            # unknown is not a licence to guess
                ('Corner House Gulberg has a swimming pool', 'amenities'),
                ('Corner House Gulberg comes with easy installments', 'instalments'),
                ('Corner House Gulberg is a verified property', 'verification'),
                ('Corner House Gulberg is in Karachi', 'city')):
            self.assertEqual(self.codes(reply), [code], reply)

    def test_facts_swapped_between_listings_are_caught(self):
        self.assertEqual(self.codes('Sky View Flat: PKR 25,000,000'), ['price'])
        self.assertEqual(self.codes('Sky View Flat: 10 marla'), ['size'])

    def test_continuation_paragraph_belongs_to_the_listing(self):
        self.assertEqual(self.codes('Sky View Flat is nice.\n\nThe price is 2.5 crore.'), ['price'])

    def test_negations_and_questions_are_not_claims(self):
        for reply in ('Corner House Gulberg has no swimming pool.', 'Installments are not available.',
                      'Do you need a swimming pool?', 'Sky View Flat: 3 bathrooms is not specified'):
            self.assertEqual(self.codes(reply), [], reply)

    def test_user_budget_may_be_repeated_but_not_confirmed_as_the_price(self):
        msg = 'is the corner house 2 crore?'
        self.assertEqual(self.codes('Within your budget of 2 crore: none.', message=msg), [])
        self.assertEqual(self.codes('Yes, Corner House Gulberg is 2 crore', message=msg), ['price'])

    def test_other_tools_and_market_knowledge_are_sources_of_numbers(self):
        e = self.evidence(self.house)
        e.add({'annual_tax': 250_000})
        self.assertTrue(G.check('Corner House Gulberg: annual tax PKR 250,000', e).ok)
        self.assertEqual(self.codes('Exempt up to PKR 25,000', system='Exempt up to PKR 25,000'), [])

    def test_rounding(self):
        Property.objects.filter(pk=self.house.pk).update(price=12_550_000)
        self.assertEqual(self.codes('Corner House Gulberg: 1.26 crore'), [])
        self.assertEqual(self.codes('Corner House Gulberg: 1.3 crore'), ['price'])

    def test_current_database_value_wins_and_inactive_drops(self):
        e = self.evidence(self.house)
        Property.objects.filter(pk=self.house.pk).update(price=30_000_000)
        self.assertFalse(G.check('Corner House Gulberg: 2.5 crore', e).ok)

    def test_invented_listing_without_tool(self):
        e = G.Evidence('show houses', [], '')
        self.assertEqual(G.check('1. Garden Villa, PKR 3 crore, 1 kanal, 5 bed', e).codes, ['unsourced_listing'])
        for ok in ('Prices in Lahore start around PKR 1 crore.', 'Hello! Which city?',
                   'Do you want a 5 marla house around 1 crore?'):
            self.assertTrue(G.check(ok, e).ok, ok)
        e = G.Evidence('sell my 10 marla house for 2 crore', [], '')
        self.assertTrue(G.check('Your 10 marla house at 2 crore — which area?', e).ok)

    def test_follow_up_uses_last_results_of_the_same_conversation_only(self):
        G.check('x', self.evidence(self.house))
        self.assertTrue(G.check('It is 2.5 crore with 4 bedrooms', G.Evidence('price?', [], '')).ok)
        self.assertFalse(G.check('It is 2.2 crore', G.Evidence('price?', [], '')).ok)
        set_context(None, '923000000001')
        self.assertEqual(G.Evidence('price?', [], '').listings(), [])


class RuntimeTest(Base):
    def run_turn(self, *steps):
        def search(city: str = '') -> dict:
            """Search."""
            return self.rows(self.house)
        b = Scripted('gemini', [calls(('search', {})), *steps])
        return runtime.run_turn(b, 'show houses', [], [R.ToolSpec('search', func=search)], 'sys'), b

    def test_pass(self):
        turn, _ = self.run_turn(final('Corner House Gulberg: PKR 2.5 crore'))
        self.assertEqual((turn.grounding, turn.citations, turn.steps), ('passed', [str(self.house.pk)], 2))

    def test_one_regeneration(self):
        turn, b = self.run_turn(final('Corner House Gulberg: 2 crore'), final('Corner House Gulberg: 2.5 crore'))
        self.assertEqual((turn.text, turn.grounding, turn.steps), ('Corner House Gulberg: 2.5 crore', 'corrected', 3))
        self.assertEqual((turn.grounding_codes, turn.prompt_tokens), (['price'], 17))
        self.assertIn('20,000,000', b.sent[-1][-1]['text'])
        self.assertFalse(ComplianceViolation.objects.exists())

    def test_second_failure_sends_facts_only(self):
        turn, b = self.run_turn(final('Corner House Gulberg: 2 crore'), final('Corner House Gulberg: 1 crore'))
        self.assertEqual((turn.grounding, b.steps), ('fallback', []))
        self.assertIn('PKR 25,000,000 · 10 marla · 4 bed · 5 bath', turn.text)
        self.assertIn('connect you with an agent', turn.text)
        self.assertEqual(ComplianceViolation.objects.get().violation_type, 'ungrounded_reply')
        self.assertTrue(G.check(turn.text, self.evidence(self.house)).ok)

    def test_provider_failure_or_tool_call_during_regeneration(self):
        for step in (ConnectionError('x'), calls(('search', {})), final('  ')):
            turn, _ = self.run_turn(final('Corner House Gulberg: 2 crore'), step)
            self.assertEqual(turn.grounding, 'fallback')

    def test_checker_crash_never_sends_the_unchecked_reply(self):
        with patch('apps.ai.grounding.check', side_effect=RuntimeError('x')):
            turn, _ = self.run_turn(final('Corner House Gulberg: 2 crore'))
        self.assertEqual((turn.text, turn.grounding), (runtime.GROUNDING_ERROR_REPLY, 'error'))

    def test_trace_and_focus_check_skipped(self):
        from apps.ai.models import AITrace
        from apps.ai.schemas import IntentResult
        from apps.ai.service import AIServiceManager
        b = Scripted('gemini', [Step('Garden Villa: 3 crore, 1 kanal', [], 'm'), Step('Which city?', [], 'm')])
        with override_settings(AI_PROVIDER_ORDER=['gemini']), patch('apps.ai.llm.make_backend', return_value=b), \
                patch('apps.ai.service.IntentClassifier.classify',
                      return_value=IntentResult(intent='general_query', confidence=0.5)), \
                patch('apps.ai.compliance_filters.ComplianceEngine.check_output') as old:
            reply = AIServiceManager().process(PHONE, 'show villas', None)
        old.assert_not_called()
        t = AITrace.objects.get()
        self.assertEqual((reply, t.grounding_result, t.grounding_codes),
                         ('Which city?', 'corrected', ['unsourced_listing']))


class PromptSampleTest(Base):
    def test_inventory_sample_has_names_only(self):
        from apps.ai.context import DynamicContextBuilder
        block = DynamicContextBuilder._org_block(self.org)
        self.assertIn('Corner House Gulberg', block)
        self.assertNotIn('25,000,000', block)


class AmlSessionBlockTest(TestCase):
    """KI-060: sessions created by the router (phone without '+') must be blocked."""

    def test_blocked_buyer_sessions(self):
        from types import SimpleNamespace

        from apps.compliance.compliance_service import ComplianceService
        from apps.whatsapp.models import WhatsAppSession
        buyer = make_client(phone='+' + PHONE)
        mine = WhatsAppSession.objects.create(phone=PHONE, user=buyer)
        orphan = WhatsAppSession.objects.create(phone=PHONE)
        other = WhatsAppSession.objects.create(phone='923000000002')
        deal = SimpleNamespace(id='d', status='', save=lambda **k: None)
        ComplianceService._handle_blocked(deal, buyer, SimpleNamespace(status='blocked', risk_score=99), None)
        modes = dict(WhatsAppSession.objects.values_list('pk', 'conversation_mode'))
        self.assertEqual((modes[mine.pk], modes[orphan.pk], modes[other.pk]), ('BLOCKED', 'BLOCKED', 'AI_MANAGED'))
