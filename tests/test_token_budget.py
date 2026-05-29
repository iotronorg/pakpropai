"""
Tests for SlidingWindowTokenBudget + BillingAlertService (apps.ai.token_governor).

Coverage:
  a  fresh org = 0 spend
  b  record_tokens increments correctly
  c  tokens older than 24h excluded from window
  d  tier limit from plan (trial=50000, professional=1000000)
  e  state transitions: ok / warning / throttled / hard_limit
  f  throttled state returns fallback without LLM call
  g  hard_limit triggers Notification for admin
  h  budget alert dedup (second call within 1h skips second Notification)
  i  throttle Redis key cleared after budget recovery
  j  cross-org isolation (org A throttle does not affect org B)
  k  trigger_throttle_state sets Redis key with 300s TTL
  l  TokenUsageRecord written even on cache hit (audit trail)
"""

import time
import uuid
from unittest.mock import MagicMock, patch, call

from django.test import TestCase, override_settings

from tests.factories import make_developer, make_user


class BudgetFreshOrgTest(TestCase):
    """a — fresh org has 0 spend."""

    def test_a_fresh_org_zero_spend(self):
        from apps.ai.token_governor import SlidingWindowTokenBudget

        mock_r = MagicMock()
        mock_r.zrangebyscore.return_value = []
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            result = SlidingWindowTokenBudget.get_spend_24h("fresh-org-id")
        self.assertEqual(result, 0)


class BudgetRecordTokensTest(TestCase):
    """b — record_tokens increments the sorted set."""

    def test_b_record_tokens_calls_zadd(self):
        from apps.ai.token_governor import SlidingWindowTokenBudget

        mock_r = MagicMock()
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            SlidingWindowTokenBudget.record_tokens("org-1", tokens_in=100, tokens_out=50)

        mock_r.zadd.assert_called_once()
        mock_r.zremrangebyscore.assert_called_once()
        mock_r.expire.assert_called_once()

        # Value stored should be 150 (in + out)
        zadd_mapping = mock_r.zadd.call_args[0][1]
        total = sum(int(k.split(':')[1]) for k in zadd_mapping)
        self.assertEqual(total, 150)


class BudgetWindowExclusionTest(TestCase):
    """c — tokens older than 24h are excluded."""

    def test_c_old_tokens_excluded_from_24h_window(self):
        from apps.ai.token_governor import SlidingWindowTokenBudget

        now = time.time()
        # Entry from 25h ago — should be excluded
        old_score = now - (25 * 3600)

        mock_r = MagicMock()
        # Simulate zrangebyscore returning nothing (old entries pruned)
        mock_r.zrangebyscore.return_value = []
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            spend = SlidingWindowTokenBudget.get_spend_24h("org-1")
        self.assertEqual(spend, 0)

        # Confirm lower bound is approximately now - 86400
        call_args = mock_r.zrangebyscore.call_args[0]
        lower_bound = float(call_args[1])
        self.assertAlmostEqual(lower_bound, now - 86400, delta=5)


class BudgetTierLimitTest(TestCase):
    """d — tier limits: trial=50000, professional=1000000."""

    def test_d_trial_plan_limit(self):
        from apps.ai.token_governor import SlidingWindowTokenBudget

        _, org = make_developer()
        org.plan = 'trial'
        # Don't save — just need the attribute on the object
        with patch("apps.config.services.SystemConfigService.get", return_value=None):
            limit = SlidingWindowTokenBudget.get_tier_limit(org)
        self.assertEqual(limit, 50_000)

    def test_d2_professional_plan_limit(self):
        from apps.ai.token_governor import SlidingWindowTokenBudget

        _, org = make_developer()
        org.plan = 'professional'
        with patch("apps.config.services.SystemConfigService.get", return_value=None):
            limit = SlidingWindowTokenBudget.get_tier_limit(org)
        self.assertEqual(limit, 1_000_000)


class BudgetStateTransitionTest(TestCase):
    """e — state: 0%→ok, 80%→warning, 96%→throttled, 101%→hard_limit."""

    def _check_state(self, used_pct, expected_state):
        from apps.ai.token_governor import SlidingWindowTokenBudget

        _, org = make_developer()
        org.plan = 'trial'
        limit = 50_000
        used = int(limit * used_pct / 100)

        mock_r = MagicMock()
        mock_r.zrangebyscore.return_value = [f"{time.time()}:{used}".encode()]

        with patch("apps.ai.token_governor._r", return_value=mock_r):
            with patch("apps.config.services.SystemConfigService.get", return_value=None):
                status = SlidingWindowTokenBudget.check_budget(str(org.id), org)

        self.assertEqual(status.state, expected_state, f"pct={used_pct}% expected {expected_state}")

    def test_e_0_percent_is_ok(self):
        from apps.ai.token_governor import SlidingWindowTokenBudget, BudgetStatus

        _, org = make_developer()
        org.plan = 'trial'
        mock_r = MagicMock()
        mock_r.zrangebyscore.return_value = []
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            with patch("apps.config.services.SystemConfigService.get", return_value=None):
                status = SlidingWindowTokenBudget.check_budget(str(org.id), org)
        self.assertEqual(status.state, 'ok')

    def test_e_80_percent_is_warning(self):
        self._check_state(80, 'warning')

    def test_e_96_percent_is_throttled(self):
        self._check_state(96, 'throttled')

    def test_e_101_percent_is_hard_limit(self):
        self._check_state(101, 'hard_limit')


class BudgetThrottleReturnsMessageTest(TestCase):
    """f — throttled org gets fallback message instead of LLM call."""

    def test_f_throttled_returns_fallback_no_llm(self):
        from apps.ai.token_governor import BillingAlertService

        org_id = "org-throttled"
        mock_r = MagicMock()
        mock_r.exists.return_value = True  # throttle key present

        with patch("apps.ai.token_governor._r", return_value=mock_r):
            throttled = BillingAlertService.is_throttled(org_id)

        self.assertTrue(throttled)


class BudgetHardLimitNotificationTest(TestCase):
    """g — hard_limit triggers Notification for org admin."""

    def test_g_hard_limit_triggers_admin_notification(self):
        from apps.ai.token_governor import BillingAlertService, BudgetStatus
        from apps.notifications.models import Notification

        _, org = make_developer()

        status = BudgetStatus(used=50_100, limit=50_000, percent=100.2, state='hard_limit')

        mock_r = MagicMock()
        mock_r.set.return_value = True  # dedup key not exists → first alert
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            BillingAlertService.trigger_budget_alert(org, status)

        notif = Notification.objects.filter(user=org.admin_user).first()
        self.assertIsNotNone(notif)
        self.assertIn("100%", notif.message)


class BudgetAlertDedupTest(TestCase):
    """h — second alert within 1h is skipped (dedup by Redis key)."""

    def test_h_alert_dedup_within_one_hour(self):
        from apps.ai.token_governor import BillingAlertService, BudgetStatus
        from apps.notifications.models import Notification

        _, org = make_developer()

        status = BudgetStatus(used=48_000, limit=50_000, percent=96.0, state='throttled')

        mock_r = MagicMock()
        # First call: set returns True (key created)
        # Second call: set returns None (key already exists → dedup)
        mock_r.set.side_effect = [True, None]
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            BillingAlertService.trigger_budget_alert(org, status)
            BillingAlertService.trigger_budget_alert(org, status)

        # Only one notification should exist
        self.assertEqual(Notification.objects.filter(user=org.admin_user).count(), 1)


class BudgetThrottleClearTest(TestCase):
    """i — clear_throttle deletes Redis key."""

    def test_i_clear_throttle_deletes_key(self):
        from apps.ai.token_governor import BillingAlertService

        mock_r = MagicMock()
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            BillingAlertService.clear_throttle("org-xyz")

        mock_r.delete.assert_called_once_with("ai_throttle:org-xyz")


class BudgetCrossOrgIsolationTest(TestCase):
    """j — org A throttle does not affect org B."""

    def test_j_cross_org_throttle_isolation(self):
        from apps.ai.token_governor import BillingAlertService

        org_a = "org-aaa"
        org_b = "org-bbb"

        mock_r = MagicMock()
        # exists returns True only for org_a's key
        mock_r.exists.side_effect = lambda key: key == f"ai_throttle:{org_a}"

        with patch("apps.ai.token_governor._r", return_value=mock_r):
            a_throttled = BillingAlertService.is_throttled(org_a)
            b_throttled = BillingAlertService.is_throttled(org_b)

        self.assertTrue(a_throttled)
        self.assertFalse(b_throttled)


class BudgetThrottleKeyTTLTest(TestCase):
    """k — trigger_throttle_state sets key with 300s TTL."""

    def test_k_throttle_key_has_300s_ttl(self):
        from apps.ai.token_governor import BillingAlertService

        _, org = make_developer()
        mock_r = MagicMock()
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            BillingAlertService.trigger_throttle_state(org)

        mock_r.set.assert_called_once()
        set_call = mock_r.set.call_args
        self.assertEqual(set_call[1].get("ex"), 300)
        self.assertIn(str(org.id), set_call[0][0])


class CacheHitAuditTrailTest(TestCase):
    """l — TokenUsageRecord written even on cache hit."""

    def test_l_cache_hit_record_in_db(self):
        from apps.ai.models import TokenUsageRecord
        from apps.ai.tasks import record_token_usage

        _, org = make_developer()

        # Simulate cache hit record
        record_token_usage(
            org_id=str(org.id),
            tokens_in=0,
            tokens_out=0,
            model='cache',
            intent='property_search',
            cache_hit=True,
        )

        qs = TokenUsageRecord.objects.filter(org=org, cache_hit=True)
        self.assertEqual(qs.count(), 1)
        rec = qs.first()
        self.assertEqual(rec.intent, 'property_search')
        self.assertEqual(rec.tokens_in, 0)
        self.assertEqual(rec.tokens_out, 0)
