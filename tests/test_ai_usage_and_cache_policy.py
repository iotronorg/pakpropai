"""
T-0006 (real token accounting). The answer cache policy is in test_faq.py (T-2016).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase

from apps.ai import usage as usage_mod
from apps.ai.schemas import IntentResult
from apps.ai.service import AIServiceManager
from tests.factories import make_developer


def _intent(name='general_query', language='en'):
    return IntentResult(intent=name, confidence=0.5, language=language)


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
