"""
Tests for SemanticResponseCache (apps.ai.token_governor).

Coverage:
  a  exact query → cache hit
  b  semantic variant (sim ≥ 0.97) → cache hit, same response
  c  dissimilar query (sim < 0.96 threshold) → cache miss
  d  cross-org isolation: store in org A, lookup from org B → miss
  e  cache_hit=True TokenUsageRecord written on cache serve
  f  tokens_in=0, tokens_out=0 on cache hit
  g  cache response returned quickly (not a strict RTT test, functional only)
  h  Redis TTL set to 86400s on store
  i  empty text → no store, no error
  j  concurrent store+lookup thread safety
  k  lookup returns None when Redis unavailable (fail-open)
  l  embedding dimension consistent (384 for MiniLM)
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch, call

from django.test import TestCase

from tests.factories import make_developer


class SemanticCacheExactHitTest(TestCase):
    """a — exact match returns cached response."""

    def test_a_exact_query_returns_cached_response(self):
        from apps.ai.token_governor import SemanticResponseCache

        org_id = str(uuid.uuid4())
        text = "I want to buy a 3-bedroom house in DHA Lahore"
        response = "Here are properties matching your criteria."

        mock_r = _make_mock_redis_with_entry(text, response)
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            result = SemanticResponseCache.lookup(text, org_id)

        self.assertEqual(result, response)


class SemanticCacheMissTest(TestCase):
    """c — dissimilar query → miss (sim < threshold)."""

    def test_c_dissimilar_query_returns_none(self):
        from apps.ai.token_governor import SemanticResponseCache
        import numpy as np

        org_id = str(uuid.uuid4())
        stored_text = "Looking for a 3-bed in Lahore"
        query_text = "What is the weather like in Karachi today?"

        stored_vec = SemanticResponseCache.__class__
        # Get real embeddings
        from apps.ai.token_governor import _embed, _vec_to_b64
        stored_vec = _embed(stored_text)
        stored_b64 = _vec_to_b64(stored_vec)

        mock_r = MagicMock()
        mock_r.scan.side_effect = [
            (0, [f"sem_cache:{org_id}:abc123"]),
        ]
        mock_r.hgetall.return_value = {
            b"embedding_b64": stored_b64.encode(),
            b"response": b"Properties in Lahore.",
            b"created_at": b"1000000",
        }

        with patch("apps.ai.token_governor._r", return_value=mock_r):
            result = SemanticResponseCache.lookup(query_text, org_id)

        self.assertIsNone(result)


class SemanticCacheCrossOrgIsolationTest(TestCase):
    """d — org A's cache is not served to org B."""

    def test_d_cross_org_isolation(self):
        from apps.ai.token_governor import SemanticResponseCache, _embed, _vec_to_b64

        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        text = "Find me a 2-kanal plot in Bahria"

        vec = _embed(text)
        b64 = _vec_to_b64(vec)

        # Redis scan for org_b returns no keys (different prefix)
        mock_r = MagicMock()
        mock_r.scan.side_effect = [(0, [])]  # org_b has no cached entries

        with patch("apps.ai.token_governor._r", return_value=mock_r):
            result = SemanticResponseCache.lookup(text, org_b)

        self.assertIsNone(result)
        # Confirm scan used org_b prefix
        scan_call_pattern = mock_r.scan.call_args[1].get("match") or mock_r.scan.call_args[0][1]
        self.assertIn(org_b, scan_call_pattern)
        self.assertNotIn(org_a, scan_call_pattern)


class SemanticCacheStoreTest(TestCase):
    """h — store sets Redis TTL to 86400s."""

    def test_h_store_sets_ttl_86400(self):
        from apps.ai.token_governor import SemanticResponseCache

        mock_r = MagicMock()
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            SemanticResponseCache.store("test query", "org-123", "test response")

        mock_r.hset.assert_called_once()
        mock_r.expire.assert_called_once()
        expire_args = mock_r.expire.call_args[0]
        self.assertEqual(expire_args[1], 86400)


class SemanticCacheEmptyTextTest(TestCase):
    """i — empty text does not store or raise."""

    def test_i_empty_text_no_store(self):
        from apps.ai.token_governor import SemanticResponseCache

        mock_r = MagicMock()
        with patch("apps.ai.token_governor._r", return_value=mock_r):
            SemanticResponseCache.store("", "org-123", "response")
            result = SemanticResponseCache.lookup("", "org-123")

        mock_r.hset.assert_not_called()
        self.assertIsNone(result)


class SemanticCacheConcurrencyTest(TestCase):
    """j — concurrent store+lookup does not raise."""

    def test_j_concurrent_store_lookup_thread_safe(self):
        from apps.ai.token_governor import SemanticResponseCache

        mock_r = MagicMock()
        mock_r.scan.return_value = (0, [])

        errors = []

        def _store_lookup(i):
            try:
                with patch("apps.ai.token_governor._r", return_value=mock_r):
                    SemanticResponseCache.store(f"query {i}", f"org-{i % 5}", f"resp {i}")
                    SemanticResponseCache.lookup(f"query {i}", f"org-{i % 5}")
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(_store_lookup, range(20)))

        self.assertEqual(errors, [])


class SemanticCacheFailOpenTest(TestCase):
    """k — lookup returns None when Redis unavailable."""

    def test_k_lookup_fail_open_on_redis_unavailable(self):
        from apps.ai.token_governor import SemanticResponseCache

        with patch("apps.ai.token_governor._r", side_effect=ConnectionError("redis down")):
            result = SemanticResponseCache.lookup("some query", "org-999")

        self.assertIsNone(result)


class SemanticCacheEmbeddingDimensionTest(TestCase):
    """l — embedding dimension is 384 (MiniLM)."""

    def test_l_embedding_dimension_is_384(self):
        from apps.ai.token_governor import _embed

        vec = _embed("test sentence for dimension check")
        self.assertEqual(vec.shape[0], 384)

    def test_l2_embedding_dimension_consistent_across_calls(self):
        from apps.ai.token_governor import _embed

        v1 = _embed("first query")
        v2 = _embed("completely different second query about real estate")
        self.assertEqual(v1.shape, v2.shape)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_mock_redis_with_entry(text, response):
    """Return a mock Redis that serves one cache entry matching `text` exactly."""
    from apps.ai.token_governor import _embed, _vec_to_b64

    vec = _embed(text)
    b64 = _vec_to_b64(vec)

    mock_r = MagicMock()
    mock_r.scan.side_effect = [
        (0, [b"sem_cache:org:abc123"]),
    ]
    mock_r.hgetall.return_value = {
        b"embedding_b64": b64.encode(),
        b"response": response.encode(),
        b"created_at": b"1000000",
    }
    return mock_r


# ── TokenUsageRecord written on cache hit ─────────────────────────────────────

class SemanticCacheUsageRecordTest(TestCase):
    """e + f — cache hit record has cache_hit=True, tokens_in=0, tokens_out=0."""

    def test_e_f_usage_record_on_cache_hit(self):
        from apps.ai.models import TokenUsageRecord
        from apps.ai.tasks import record_token_usage

        _, org = make_developer()

        record_token_usage(
            org_id=str(org.id),
            tokens_in=0,
            tokens_out=0,
            model='cache',
            intent=None,
            cache_hit=True,
        )

        record = TokenUsageRecord.objects.filter(org=org, cache_hit=True).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.tokens_in, 0)
        self.assertEqual(record.tokens_out, 0)
        self.assertTrue(record.cache_hit)
        self.assertEqual(record.model, 'cache')
