"""
T-0009 — ComplianceEngine wired into AIServiceManager.process.

The LLM is mocked: it calls the real search_properties tool (which registers
the property in focus) and then returns a reply making claims about it.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.ai.schemas import IntentResult
from apps.ai.service import AIServiceManager
from tests.factories import make_developer, make_property

PHONE = '+923001110001'


class ComplianceWiringTest(TestCase):

    def setUp(self):
        _, self.org = make_developer()
        self.org.operational_mode = 'production'
        self.org.save(update_fields=['operational_mode'])
        self.prop = make_property(org=self.org, title='DHA house', city='Lahore',
                                  price=25_000_000, installment_available=False)

    def _run(self, llm_reply: str, search_city='Lahore'):
        def fake_chat(phone, message, *args, **kwargs):
            from apps.ai._tools_search import search_properties
            search_properties(city=search_city)
            return llm_reply

        agent = MagicMock()
        agent._load_history.return_value = []
        agent.chat.side_effect = fake_chat
        svc = AIServiceManager()
        svc._agent = agent
        intent = IntentResult(intent='general_query', confidence=0.5)
        with patch('apps.ai.service.IntentClassifier.classify', return_value=intent), \
             patch('apps.ai.context.DynamicContextBuilder.build', return_value=''), \
             patch('apps.ai.tasks.record_token_usage.delay'), \
             patch('apps.properties.search.PropertySearchService._apply_ai_verdicts'):
            # The service sets tool context; agent.chat is mocked so set it here too.
            from apps.ai._tools_context import set_context
            orig = agent.chat.side_effect

            def with_ctx(phone, message, *args, **kw):
                set_context(None, phone, org=self.org, message=message)
                return orig(phone, message, *args, **kw)
            agent.chat.side_effect = with_ctx
            return svc.process(PHONE, 'tell me about the DHA house', organization=self.org)

    def test_hallucinated_price_blocked(self):
        reply = self._run('This DHA house in Lahore is available for PKR 1.5 crore.')
        self.assertIn('property specialists', reply)
        from apps.audit.models import ComplianceViolation
        self.assertTrue(ComplianceViolation.objects.filter(phone__contains='3001110001').exists())

    def test_correct_price_passes(self):
        reply = self._run('This DHA house in Lahore is listed at PKR 2.5 crore.')
        self.assertIn('2.5 crore', reply)

    def test_false_installment_offer_blocked(self):
        reply = self._run('Great news — easy installments are available on this house!')
        self.assertIn('property specialists', reply)

    def test_no_property_in_focus_is_not_blocked(self):
        make_property(org=self.org, title='Second', city='Lahore', location='Gulberg', price=9_000_000)
        reply = self._run('Prices in Lahore start around PKR 1 crore.')
        self.assertIn('1 crore', reply)
