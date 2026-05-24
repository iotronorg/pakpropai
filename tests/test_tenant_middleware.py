"""
Unit tests for TenantDomainMiddleware.

Covers:
  1. Platform domain and local dev hostnames → no resolution, request passes through
  2. Valid subdomain ({slug}.realtron.ai) → request.tenant_org set correctly
  3. Unrecognized subdomain → request.tenant_org is None, request passes through
  4. Valid custom domain (portal.imarat.ai) → request.tenant_org set correctly
  5. Unrecognized custom domain → hard 404 JSON response
  6. Redis cache hit → org resolved without DB query
  7. Redis cache miss → DB queried, result cached for subsequent requests
  8. Inactive organization → treated as not found
  9. Performance: resolution adds < 12 ms overhead on cache-hit path
"""
import time
from unittest.mock import MagicMock, patch, call

from django.test import TestCase, RequestFactory, override_settings

from apps.core.middleware import TenantDomainMiddleware
from apps.organizations.models import Organization


PLATFORM_DOMAIN = 'realtron.ai'


def _make_response(status=200):
    resp = MagicMock()
    resp.status_code = status
    return resp


def _middleware(get_response=None):
    if get_response is None:
        get_response = lambda r: _make_response()
    return TenantDomainMiddleware(get_response)


def _request(host: str) -> MagicMock:
    req = MagicMock()
    req.get_host.return_value = host
    req.path = '/api/v1/external/leads/'
    return req


# ── 1. Platform / local hostnames ─────────────────────────────────────────────

@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN)
class TestPlatformHostnamePassthrough(TestCase):

    def test_platform_own_domain_passes_through(self):
        mw = _middleware()
        req = _request('realtron.ai')
        mw(req)
        self.assertIsNone(req.tenant_org)

    def test_localhost_passes_through(self):
        mw = _middleware()
        req = _request('localhost')
        mw(req)
        self.assertIsNone(req.tenant_org)

    def test_127_0_0_1_passes_through(self):
        mw = _middleware()
        req = _request('127.0.0.1')
        mw(req)
        self.assertIsNone(req.tenant_org)

    def test_testserver_passes_through(self):
        mw = _middleware()
        req = _request('testserver')
        mw(req)
        self.assertIsNone(req.tenant_org)

    def test_host_with_port_stripped_correctly(self):
        mw = _middleware()
        req = _request('localhost:8000')
        mw(req)
        self.assertIsNone(req.tenant_org)


# ── 2. Subdomain resolution ───────────────────────────────────────────────────

@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN)
class TestSubdomainResolution(TestCase):

    def setUp(self):
        self.org = Organization.objects.create(name='Acme Realty')
        # slug is auto-generated from name → 'acme-realty'

    def test_valid_subdomain_resolves_tenant_org(self):
        mw = _middleware()
        slug = self.org.slug
        req = _request(f'{slug}.{PLATFORM_DOMAIN}')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            mw(req)

        self.assertIsNotNone(req.tenant_org)
        self.assertEqual(req.tenant_org.pk, self.org.pk)

    def test_unrecognized_subdomain_passes_through(self):
        mw = _middleware()
        req = _request(f'nonexistent-xyz-404.{PLATFORM_DOMAIN}')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            response = mw(req)

        # Must NOT return a 404 — pass through to URL routing
        self.assertIsNone(req.tenant_org)
        # response is the get_response mock result, not a hard 404
        self.assertEqual(response.status_code, 200)

    def test_inactive_org_subdomain_not_resolved(self):
        self.org.is_active = False
        self.org.save(update_fields=['is_active'])

        mw = _middleware()
        req = _request(f'{self.org.slug}.{PLATFORM_DOMAIN}')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            response = mw(req)

        self.assertIsNone(req.tenant_org)
        self.assertEqual(response.status_code, 200)  # pass-through


# ── 3. Custom domain resolution ───────────────────────────────────────────────

@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN)
class TestCustomDomainResolution(TestCase):

    def setUp(self):
        self.org = Organization.objects.create(
            name='Imarat Group',
            custom_domain='portal.imarat.ai',
        )

    def test_valid_custom_domain_resolves_tenant_org(self):
        mw = _middleware()
        req = _request('portal.imarat.ai')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            mw(req)

        self.assertIsNotNone(req.tenant_org)
        self.assertEqual(req.tenant_org.pk, self.org.pk)

    def test_unrecognized_custom_domain_returns_404(self):
        mw = _middleware()
        req = _request('unknown.competitor.com')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            response = mw(req)

        # Must short-circuit with 404 JSON
        self.assertEqual(response.status_code, 404)
        import json
        self.assertIn('not recognized', response.content.decode())

    def test_custom_domain_case_insensitive(self):
        mw = _middleware()
        req = _request('PORTAL.IMARAT.AI')  # uppercase

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            mw(req)

        self.assertIsNotNone(req.tenant_org)
        self.assertEqual(req.tenant_org.pk, self.org.pk)

    def test_inactive_org_custom_domain_returns_404(self):
        self.org.is_active = False
        self.org.save(update_fields=['is_active'])

        mw = _middleware()
        req = _request('portal.imarat.ai')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            response = mw(req)

        # Inactive org → custom domain resolves to nothing → 404
        self.assertEqual(response.status_code, 404)


# ── 4. Cache behaviour ────────────────────────────────────────────────────────

@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN)
class TestTenantDomainCache(TestCase):

    def setUp(self):
        self.org = Organization.objects.create(
            name='Cache Test Org',
            custom_domain='cache.test.ai',
        )

    def test_cache_hit_skips_db_query(self):
        mw = _middleware()
        req = _request('cache.test.ai')

        # Simulate a warm cache hit with the org's PK
        with patch('django.core.cache.cache.get', return_value=str(self.org.pk)), \
             patch('apps.organizations.models.Organization.objects') as mock_qs:
            mock_qs.filter.return_value.only.return_value.first.return_value = self.org
            mock_qs.only.return_value.get.return_value = self.org

            mw(req)

        # DB was hit only for the PK-based get (not for filter+slug lookup)
        mock_qs.filter.assert_not_called()

    def test_cache_miss_populates_cache(self):
        mw = _middleware()
        req = _request('cache.test.ai')

        captured_sets = []

        def fake_cache_set(key, value, ttl):
            captured_sets.append((key, value, ttl))

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set', side_effect=fake_cache_set):
            mw(req)

        # A cache.set call must have been made with the org's PK
        self.assertTrue(
            any(str(self.org.pk) in str(v) for _, v, _ in captured_sets),
            f"Expected org PK in cache.set calls: {captured_sets}",
        )

    def test_unknown_domain_cached_as_sentinel(self):
        mw = _middleware()
        req = _request('totally-unknown-xyz.com')

        captured_sets = []

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set', side_effect=lambda k, v, t: captured_sets.append((k, v, t))):
            mw(req)

        # The __none__ sentinel must be cached to avoid repeated DB misses
        self.assertTrue(
            any(v == '__none__' for _, v, _ in captured_sets),
            f"Expected __none__ sentinel in cache.set calls: {captured_sets}",
        )

    def test_sentinel_cache_hit_returns_none_without_db(self):
        mw = _middleware()
        req = _request('totally-unknown-xyz.com')

        # Cache returns the sentinel — DB must not be touched
        with patch('django.core.cache.cache.get', return_value='__none__'), \
             patch('apps.organizations.models.Organization.objects') as mock_qs:
            mw(req)

        mock_qs.filter.assert_not_called()


# ── 5. Org data scoping correctness ───────────────────────────────────────────

@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN)
class TestTenantOrgDataScoping(TestCase):
    """
    Two orgs with different custom domains must resolve independently.
    Org A's domain must never resolve to Org B's tenant context.
    """

    def setUp(self):
        self.org_a = Organization.objects.create(
            name='Org A', custom_domain='a.propfirm.com'
        )
        self.org_b = Organization.objects.create(
            name='Org B', custom_domain='b.propfirm.com'
        )

    def test_org_a_domain_resolves_to_org_a(self):
        mw = _middleware()
        req = _request('a.propfirm.com')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            mw(req)

        self.assertEqual(req.tenant_org.pk, self.org_a.pk)

    def test_org_b_domain_resolves_to_org_b(self):
        mw = _middleware()
        req = _request('b.propfirm.com')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            mw(req)

        self.assertEqual(req.tenant_org.pk, self.org_b.pk)

    def test_org_a_domain_does_not_resolve_to_org_b(self):
        mw = _middleware()
        req = _request('a.propfirm.com')

        with patch('django.core.cache.cache.get', return_value=None), \
             patch('django.core.cache.cache.set'):
            mw(req)

        self.assertNotEqual(req.tenant_org.pk, self.org_b.pk)


# ── 6. Performance constraint ─────────────────────────────────────────────────

@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN)
class TestTenantDomainPerformance(TestCase):
    """
    Cache-hit resolution must add < 12 ms overhead.
    DB-fallback resolution should ideally be < 12 ms too, but we
    only assert the hard SLA on the cache-hit path since DB latency
    is environment-dependent.
    """

    def setUp(self):
        self.org = Organization.objects.create(
            name='Perf Org', custom_domain='perf.example.com'
        )

    def test_cache_hit_resolution_under_12ms(self):
        mw = _middleware()

        # Warm the actual Django cache to simulate a real cache hit
        from django.core.cache import cache
        cache.set(f'tenant_domain:perf.example.com', str(self.org.pk), 300)

        iterations = 20
        req = _request('perf.example.com')
        req.tenant_org = None

        start = time.monotonic()
        for _ in range(iterations):
            req.tenant_org = None
            mw(req)
        elapsed_ms = (time.monotonic() - start) / iterations * 1000

        self.assertLess(
            elapsed_ms, 12.0,
            f"Cache-hit resolution took {elapsed_ms:.2f} ms — exceeds 12 ms SLA",
        )
        # Clean up
        cache.delete('tenant_domain:perf.example.com')
