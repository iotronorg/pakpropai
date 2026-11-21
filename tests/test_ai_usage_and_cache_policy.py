"""
T-0005 (semantic cache leakage) and T-0006 (real token accounting).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase

from apps.ai import usage as usage_mod
from apps.ai.schemas import IntentResult
from apps.ai.service import AIServiceManager, semantic_cache_scope
from apps.config.models import SystemConfig
from tests.factories import make_developer


def _intent(name='general_query', language='en'):
    return IntentResult(intent=name, confidence=0.5, language=language)


def _enable_cache():
    SystemConfig.objects.update_or_create(key='semantic_cache_enabled', defaults={'value': 'true'})
    cache.clear()


class UsageCaptureTest(TestCase):

    def test_record_without_capture_is_noop(self):
        usage_mod._current.set(None)
        usage_mod.record_usage('m', 10, 5)
        self.assertEqual(usage_mod.current_usage().total, 0)

    def test_accumulates_across_calls(self):
        usage_mod.start_capture()
        usage_mod.record_usage('a', 10, 5)
        usage_mod.record_usage('b', 3, 2, cached_tokens=1)
        totals = usage_mod.current_usage()
        self.assertEqual((totals.tokens_in, totals.tokens_out, totals.cached_tokens), (13, 7, 1))
        self.assertEqual(totals.calls, 2)
        self.assertEqual(totals.model, 'b')
        self.assertTrue(totals.reported)

    def test_unreported_usage_is_not_fabricated(self):
        usage_mod.start_capture()
        usage_mod.record_gemini_response('g', SimpleNamespace(usage_metadata=None))
        totals = usage_mod.current_usage()
        self.assertEqual(totals.total, 0)
        self.assertFalse(totals.reported)
        self.assertEqual(totals.calls, 1)

    def test_gemini_and_openai_extractors(self):
        usage_mod.start_capture()
        usage_mod.record_gemini_response('g', SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=20, cached_content_token_count=None,
        )))
        usage_mod.record_openai_response('o', SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=7, completion_tokens=3,
        )))
        totals = usage_mod.current_usage()
        self.assertEqual((totals.tokens_in, totals.tokens_out), (107, 23))


class SemanticCacheScopeTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_disabled_by_default(self):
        self.assertIsNone(semantic_cache_scope('org-1', _intent(), 'how does buying work'))

    def test_enabled_general_query_without_digits(self):
        _enable_cache()
        self.assertEqual(semantic_cache_scope('org-1', _intent(language='ur'), 'kya process hai'), 'org-1:ur')

    def test_messages_with_digits_never_cached(self):
        _enable_cache()
        self.assertIsNone(semantic_cache_scope('org-1', _intent(), 'tax on a 5 crore house'))

    def test_personal_or_inventory_intents_never_cached(self):
        _enable_cache()
        for name in ('property_search', 'deal_lock', 'talk_to_agent', 'list_property',
                     'tax_advice', 'loan_eligibility', 'property_audit', 'greeting'):
            self.assertIsNone(semantic_cache_scope('org-1', _intent(name), 'hello there'), name)

    def test_requires_org(self):
        _enable_cache()
        self.assertIsNone(semantic_cache_scope(None, _intent(), 'how does buying work'))


class ProcessPipelineTest(TestCase):
    """End-to-end through AIServiceManager.process with the LLM agent mocked."""

    def setUp(self):
        cache.clear()
        _, self.org = make_developer()
        self.agent = MagicMock()
        self.agent._load_history.return_value = []

        def fake_chat(*args, **kwargs):
            usage_mod.record_usage('gemini-test', 120, 30)
            return 'Buying usually involves an offer, due diligence and transfer.'

        self.agent.chat.side_effect = fake_chat
        self.svc = AIServiceManager()
        self.svc._agent = self.agent

    def _process(self, message='how does buying work'):
        with patch('apps.ai.service.IntentClassifier.classify', return_value=_intent()), \
             patch('apps.ai.context.DynamicContextBuilder.build', return_value=''), \
             patch('apps.ai.tasks.record_token_usage.delay') as delay:
            reply = self.svc.process('+923001112233', message, organization=self.org)
        return reply, delay

    def test_real_tokens_recorded(self):
        with patch('apps.ai.token_governor.SlidingWindowTokenBudget.record_tokens') as rec:
            reply, delay = self._process()
        self.assertIn('offer', reply)
        kwargs = delay.call_args.kwargs
        self.assertEqual((kwargs['tokens_in'], kwargs['tokens_out']), (120, 30))
        self.assertEqual(kwargs['model'], 'gemini-test')
        rec.assert_called_once_with(str(self.org.id), 120, 30)

    def test_cache_not_consulted_when_disabled(self):
        with patch('apps.ai.token_governor.SemanticResponseCache.lookup') as lookup, \
             patch('apps.ai.token_governor.SemanticResponseCache.store') as store:
            self._process()
        lookup.assert_not_called()
        store.assert_not_called()

    def test_cache_hit_when_enabled_uses_scoped_namespace(self):
        _enable_cache()
        with patch('apps.ai.token_governor.SemanticResponseCache.lookup', return_value='cached!') as lookup:
            reply, _ = self._process()
        self.assertEqual(reply, 'cached!')
        lookup.assert_called_once_with('how does buying work', f'{self.org.id}:en')
        self.agent.chat.assert_not_called()

    def test_guard_rejected_reply_is_not_cached(self):
        _enable_cache()
        with patch('apps.ai.token_governor.SemanticResponseCache.lookup', return_value=None), \
             patch('apps.ai.token_governor.SemanticResponseCache.store') as store, \
             patch('apps.ai.guardrails.GuardrailEngine.check_output', return_value=False):
            self._process()
        store.assert_not_called()
