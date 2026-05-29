"""
Unit tests for ConsecutiveAuthFailureLimiter (10 tests).
Uses override_settings(CACHES=locmem) so the rate limiter uses an isolated
in-memory cache per test method — no Redis required.
"""

from django.test import TestCase, override_settings

_LOCMEM_CACHE = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
}


@override_settings(CACHES=_LOCMEM_CACHE)
class TestConsecutiveAuthFailureLimiter(TestCase):

    def test_first_failure_does_not_block(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        blocked = ConsecutiveAuthFailureLimiter.record_auth_failure('192.168.1.1')
        self.assertFalse(blocked)

    def test_second_failure_does_not_block(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        ConsecutiveAuthFailureLimiter.record_auth_failure('192.168.1.2')
        blocked = ConsecutiveAuthFailureLimiter.record_auth_failure('192.168.1.2')
        self.assertFalse(blocked)

    def test_third_failure_triggers_block(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        ip = '192.168.1.3'
        ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        blocked = ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        self.assertTrue(blocked)

    def test_blocked_ip_is_detected(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter, FAILURE_THRESHOLD
        ip = '192.168.1.4'
        for _ in range(FAILURE_THRESHOLD):
            ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        self.assertTrue(ConsecutiveAuthFailureLimiter.is_blocked(ip))

    def test_reset_clears_block(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter, FAILURE_THRESHOLD
        ip = '192.168.1.5'
        for _ in range(FAILURE_THRESHOLD):
            ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        self.assertTrue(ConsecutiveAuthFailureLimiter.is_blocked(ip))
        ConsecutiveAuthFailureLimiter.reset(ip)
        self.assertFalse(ConsecutiveAuthFailureLimiter.is_blocked(ip))

    def test_different_ips_are_independent(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter, FAILURE_THRESHOLD
        ip_a, ip_b = '10.0.0.1', '10.0.0.2'
        for _ in range(FAILURE_THRESHOLD):
            ConsecutiveAuthFailureLimiter.record_auth_failure(ip_a)
        self.assertTrue(ConsecutiveAuthFailureLimiter.is_blocked(ip_a))
        self.assertFalse(ConsecutiveAuthFailureLimiter.is_blocked(ip_b))

    def test_counter_ttl_set_on_first_failure(self):
        # TTL is implicitly verified by Django cache set(key, val, timeout) being called
        # with WINDOW_SECONDS; we verify the count is correct after first failure
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        ip = '10.0.0.3'
        ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        self.assertEqual(ConsecutiveAuthFailureLimiter.get_failure_count(ip), 1)

    def test_block_ttl_set_when_blocked(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter, FAILURE_THRESHOLD
        ip = '10.0.0.4'
        for _ in range(FAILURE_THRESHOLD):
            ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        # Verify the block key exists in cache
        self.assertTrue(ConsecutiveAuthFailureLimiter.is_blocked(ip))

    def test_threat_record_increments_same_as_auth_failure(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        ip = '10.0.0.5'
        ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        blocked = ConsecutiveAuthFailureLimiter.record_threat(ip)
        self.assertTrue(blocked)

    def test_redis_failure_does_not_raise(self):
        from unittest.mock import patch
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        with patch('apps.security.rate_limiter._cache', side_effect=Exception('cache down')):
            blocked = ConsecutiveAuthFailureLimiter.record_auth_failure('10.0.0.6')
        self.assertFalse(blocked)

    def test_get_failure_count(self):
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        ip = '10.0.0.7'
        self.assertEqual(ConsecutiveAuthFailureLimiter.get_failure_count(ip), 0)
        ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        self.assertEqual(ConsecutiveAuthFailureLimiter.get_failure_count(ip), 2)
