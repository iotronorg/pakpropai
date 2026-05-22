"""Tests for the Redis-backed circuit breaker."""
from django.test import TestCase
from apps.core.circuit_breaker import CircuitBreaker


class CircuitBreakerTest(TestCase):

    def setUp(self):
        self.cb = CircuitBreaker(
            name='test_cb_' + self._testMethodName,
            failure_threshold=3,
            recovery_timeout=10,
        )

    def test_closed_state_calls_function(self):
        result = self.cb.call(lambda: 'ok', fallback='fallback')
        self.assertEqual(result, 'ok')

    def test_failure_increments_but_stays_closed_below_threshold(self):
        def fail(): raise Exception('boom')
        self.cb.call(fail, fallback='fb')
        self.cb.call(fail, fallback='fb')
        self.assertEqual(self.cb.get_state(), 'closed')

    def test_threshold_opens_circuit(self):
        def fail(): raise Exception('boom')
        for _ in range(3):
            self.cb.call(fail, fallback='fb')
        self.assertEqual(self.cb.get_state(), 'open')

    def test_open_circuit_returns_fallback_without_calling_fn(self):
        called = []
        def fn():
            called.append(True)
            return 'real'
        def fail(): raise Exception('boom')
        for _ in range(3):
            self.cb.call(fail, fallback='fb')
        result = self.cb.call(fn, fallback='fallback_value')
        self.assertEqual(result, 'fallback_value')
        self.assertEqual(called, [])

    def test_reset_closes_circuit(self):
        def fail(): raise Exception('boom')
        for _ in range(3):
            self.cb.call(fail, fallback='fb')
        self.cb.reset()
        self.assertEqual(self.cb.get_state(), 'closed')

    def test_success_resets_failure_count(self):
        def fail(): raise Exception('boom')
        self.cb.call(fail, fallback='fb')
        self.cb.call(fail, fallback='fb')
        self.cb.call(lambda: 'ok', fallback='fb')  # success — should reset
        self.assertEqual(self.cb.get_state(), 'closed')

    def test_fallback_returned_on_failure(self):
        def fail(): raise ValueError('nope')
        result = self.cb.call(fail, fallback='my_fallback')
        self.assertEqual(result, 'my_fallback')
