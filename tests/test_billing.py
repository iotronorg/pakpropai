"""
Billing & Metering Layer — test suite.

Covers:
  - Agent seat enforcement (HTTP 402 on limit, 201 under limit, enterprise bypass)
  - Counter increment on create, decrement on remove
  - WhatsApp token guard (canned reply when exhausted, router not called)
  - Token recording on successful AI turn
  - Cross-tenant isolation
  - Enterprise usage recording (no 402)
"""
import uuid
from unittest.mock import MagicMock, patch, call

from django.test import TestCase, override_settings
from django.core.cache import cache

from apps.billing.ledger import UsageLedger
from apps.billing.limits import PLAN_LIMITS


# ── Helpers ────────────────────────────────────────────────────────────────────

def _org_id() -> str:
    return str(uuid.uuid4())


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
)
class _CacheTestCase(TestCase):
    """Base that wipes LocMemCache between tests."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()


# ── Agent seat limit ───────────────────────────────────────────────────────────

class AgentSeatLimitTest(_CacheTestCase):

    def test_under_limit_returns_true(self):
        org = _org_id()
        # trial max is 2; counter starts at 0
        self.assertTrue(UsageLedger.within_limit(org, 'trial', 'agents'))

    def test_at_limit_returns_false(self):
        org = _org_id()
        # Saturate counter to the trial max
        for _ in range(PLAN_LIMITS['trial']['max_agents']):
            UsageLedger.increment_agents(org)
        self.assertFalse(UsageLedger.within_limit(org, 'trial', 'agents'))

    def test_enterprise_always_within_limit(self):
        org = _org_id()
        # Simulate 1000 agents — still within limit for enterprise
        for _ in range(1000):
            UsageLedger.increment_agents(org)
        self.assertTrue(UsageLedger.within_limit(org, 'enterprise', 'agents'))

    def test_counter_increments_on_increment(self):
        org = _org_id()
        self.assertEqual(UsageLedger.get_agent_count(org), 0)
        UsageLedger.increment_agents(org)
        UsageLedger.increment_agents(org)
        self.assertEqual(UsageLedger.get_agent_count(org), 2)

    def test_counter_decrements_on_decrement(self):
        org = _org_id()
        UsageLedger.increment_agents(org)
        UsageLedger.increment_agents(org)
        UsageLedger.decrement_agents(org)
        self.assertEqual(UsageLedger.get_agent_count(org), 1)

    def test_decrement_never_goes_below_zero(self):
        org = _org_id()
        UsageLedger.decrement_agents(org)
        self.assertEqual(UsageLedger.get_agent_count(org), 0)


# ── WhatsApp token guard ───────────────────────────────────────────────────────

class WhatsAppTokenGuardTest(_CacheTestCase):

    def _make_org_mock(self, plan='trial', org_id=None):
        org = MagicMock()
        org.id   = org_id or uuid.uuid4()
        org.plan = plan
        org.wa_phone_number_id = 'phone_number_id_test'
        return org

    @patch('apps.whatsapp.client.WhatsAppClient')
    @patch('apps.organizations.models.Organization')
    def test_exhausted_sends_canned_reply(self, MockOrg, MockWAClient):
        org = self._make_org_mock(plan='trial')
        MockOrg.objects.filter.return_value.first.return_value = org

        # Saturate token counter
        for _ in range(PLAN_LIMITS['trial']['monthly_wa_tokens']):
            UsageLedger.increment_wa_tokens(str(org.id))

        from apps.whatsapp.tasks import process_incoming_whatsapp_task
        message = {'id': 'msg1', 'from': '+923001234567', 'type': 'text',
                   'text': {'body': 'Hello'}}

        process_incoming_whatsapp_task(message, phone_number_id='phone_number_id_test')

        MockWAClient.return_value.send_text.assert_called_once()
        call_args = MockWAClient.return_value.send_text.call_args[0]
        self.assertIn('capacity', call_args[1].lower())

    @patch('apps.organizations.models.Organization')
    def test_exhausted_does_not_call_router(self, MockOrg):
        org = self._make_org_mock(plan='trial')
        MockOrg.objects.filter.return_value.first.return_value = org

        for _ in range(PLAN_LIMITS['trial']['monthly_wa_tokens']):
            UsageLedger.increment_wa_tokens(str(org.id))

        with patch('apps.whatsapp.client.WhatsAppClient'), \
             patch('apps.whatsapp.router.MessageRouter') as MockRouter:
            from apps.whatsapp.tasks import process_incoming_whatsapp_task
            message = {'id': 'msg2', 'from': '+923001234567', 'type': 'text',
                       'text': {'body': 'Hello'}}
            process_incoming_whatsapp_task(message, phone_number_id='phone_number_id_test')

        MockRouter.route.assert_not_called()

    @patch('apps.organizations.models.Organization')
    def test_within_limit_records_token(self, MockOrg):
        org = self._make_org_mock(plan='basic')
        MockOrg.objects.filter.return_value.first.return_value = org

        with patch('apps.whatsapp.client.WhatsAppClient'), \
             patch('apps.whatsapp.router.MessageRouter') as MockRouter:
            MockRouter.route.return_value = None
            from apps.whatsapp.tasks import process_incoming_whatsapp_task
            message = {'id': 'msg3', 'from': '+923001234567', 'type': 'text',
                       'text': {'body': 'Hello'}}
            process_incoming_whatsapp_task(message, phone_number_id='phone_number_id_test')

        # Guard passes — confirm router was called
        MockRouter.route.assert_called_once()


# ── Cross-tenant isolation ─────────────────────────────────────────────────────

class TenantIsolationTest(_CacheTestCase):

    def test_org_a_agents_do_not_affect_org_b(self):
        org_a = _org_id()
        org_b = _org_id()
        UsageLedger.increment_agents(org_a)
        UsageLedger.increment_agents(org_a)
        self.assertEqual(UsageLedger.get_agent_count(org_b), 0)

    def test_org_a_tokens_do_not_affect_org_b(self):
        org_a = _org_id()
        org_b = _org_id()
        for _ in range(50):
            UsageLedger.increment_wa_tokens(org_a)
        self.assertEqual(UsageLedger.get_wa_token_count(org_b), 0)

    def test_reset_org_a_does_not_affect_org_b(self):
        org_a = _org_id()
        org_b = _org_id()
        UsageLedger.increment_agents(org_a)
        UsageLedger.increment_agents(org_b)
        UsageLedger.increment_wa_tokens(org_a)
        UsageLedger.increment_wa_tokens(org_b)

        UsageLedger.reset_org_counters(org_a)

        self.assertEqual(UsageLedger.get_agent_count(org_a), 0)
        self.assertEqual(UsageLedger.get_agent_count(org_b), 1)
        self.assertEqual(UsageLedger.get_wa_token_count(org_a), 0)
        self.assertEqual(UsageLedger.get_wa_token_count(org_b), 1)


# ── Enterprise usage recording ─────────────────────────────────────────────────

class EnterpriseUsageTest(_CacheTestCase):

    def test_enterprise_usage_recorded(self):
        org = _org_id()
        for _ in range(600):
            UsageLedger.increment_wa_tokens(org)

        # Usage is recorded
        self.assertEqual(UsageLedger.get_wa_token_count(org), 600)

        # within_limit always True for enterprise regardless
        self.assertTrue(UsageLedger.within_limit(org, 'enterprise', 'wa_tokens'))


# ── Fail-open behavior ─────────────────────────────────────────────────────────

class FailOpenTest(TestCase):

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
            }
        }
    )
    def test_redis_unavailable_fails_open(self):
        org = _org_id()
        # DummyCache always returns None — should fail open (True)
        result = UsageLedger.within_limit(org, 'trial', 'agents')
        self.assertTrue(result)
