"""
Tests for the white-label theming engine.

Covers:
  1. Custom domain returns correct palette
  2. Different custom domain returns different palette
  3. Unconfigured org returns platform defaults
  4. Platform domain (testserver) returns defaults without DB hit
  5. Redis cache hit skips DB on second request
  6. post_save signal invalidates custom_domain cache key
  7. post_save signal invalidates subdomain cache key
  8. Unrecognized custom domain returns 404 (TenantDomainMiddleware enforcement)
  9. Subdomain resolution returns correct palette
 10. Org without custom_domain resolves via subdomain
"""
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.organizations.models import Organization, OrganizationTheme
from tests.factories import make_developer

PLATFORM_DOMAIN = 'realtron.ai'
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

THEME_URL = '/api/v1/theme/'


def _make_branded_org(custom_domain='', primary='#AA0000', slug=None):
    """Helper: create an org with a custom theme."""
    developer, org = make_developer()
    if slug:
        org.slug = slug
        org.save(update_fields=['slug'])
    if custom_domain:
        org.custom_domain = custom_domain
        org.save(update_fields=['custom_domain'])
    OrganizationTheme.objects.create(
        organization=org,
        primary_color=primary,
        secondary_color='#BB0000',
        accent_color='#CC0000',
        logo_url='https://cdn.example.com/logo.png',
    )
    return org


@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN, CACHES=_LOCMEM_CACHE)
class TenantThemeEndpointTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_custom_domain_returns_correct_palette(self):
        _make_branded_org(custom_domain='portal.alpha.com', primary='#FF0000')
        resp = self.client.get(THEME_URL, HTTP_HOST='portal.alpha.com')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['primary_color'], '#FF0000')

    def test_different_custom_domain_returns_different_palette(self):
        _make_branded_org(custom_domain='portal.alpha.com', primary='#FF0000')
        _make_branded_org(custom_domain='portal.beta.com',  primary='#00FF00')
        resp = self.client.get(THEME_URL, HTTP_HOST='portal.beta.com')
        self.assertEqual(resp.json()['primary_color'], '#00FF00')
        self.assertNotEqual(resp.json()['primary_color'], '#FF0000')

    def test_unconfigured_org_returns_platform_defaults(self):
        developer, org = make_developer()
        org.custom_domain = 'portal.noconfig.com'
        org.save(update_fields=['custom_domain'])
        resp = self.client.get(THEME_URL, HTTP_HOST='portal.noconfig.com')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['primary_color'], '#2563EB')
        self.assertEqual(resp.json()['logo_url'], '')

    def test_platform_domain_returns_defaults_without_db_hit(self):
        # testserver is treated as the platform domain — no DB lookup
        with self.assertNumQueries(0):
            resp = self.client.get(THEME_URL, HTTP_HOST='testserver')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['primary_color'], '#2563EB')


@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN, CACHES=_LOCMEM_CACHE)
class ThemeCachingTest(TestCase):

    def setUp(self):
        cache.clear()
        self.org = _make_branded_org(custom_domain='portal.cached.com', primary='#123456')

    def test_redis_cache_hit_skips_db(self):
        # Seed the theme cache with a value diverging from the DB value
        cache.set('theme_cfg:portal.cached.com', {
            'primary_color': '#CACACA',
            'secondary_color': '#BB0000',
            'accent_color': '#CC0000',
            'logo_url': '',
            'updated_at': None,
        }, 300)
        # Response must come from cache (#CACACA), not from DB (#123456)
        resp = self.client.get(THEME_URL, HTTP_HOST='portal.cached.com')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['primary_color'], '#CACACA')

    def test_post_save_invalidates_custom_domain_key(self):
        # Warm cache manually
        cache.set('theme_cfg:portal.cached.com', {'primary_color': '#OLD'}, 300)
        # Saving the theme should wipe it
        OrganizationTheme.objects.filter(organization=self.org).update(primary_color='#999999')
        theme = OrganizationTheme.objects.get(organization=self.org)
        theme.save()  # triggers post_save signal
        self.assertIsNone(cache.get('theme_cfg:portal.cached.com'))

    def test_post_save_invalidates_subdomain_key(self):
        subdomain_key = f'theme_cfg:{self.org.slug}.{PLATFORM_DOMAIN}'
        cache.set(subdomain_key, {'primary_color': '#OLD'}, 300)
        theme = OrganizationTheme.objects.get(organization=self.org)
        theme.save()
        self.assertIsNone(cache.get(subdomain_key))


@override_settings(REALTRON_PLATFORM_DOMAIN=PLATFORM_DOMAIN, CACHES=_LOCMEM_CACHE)
class ThemeHostResolutionTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_unrecognized_custom_domain_returns_404(self):
        # TenantDomainMiddleware hard-404s custom domains it cannot resolve
        resp = self.client.get(THEME_URL, HTTP_HOST='unknown.example.com')
        self.assertEqual(resp.status_code, 404)

    def test_subdomain_resolution_returns_correct_palette(self):
        org = _make_branded_org(primary='#ABCDEF', slug='betaorg')
        resp = self.client.get(THEME_URL, HTTP_HOST=f'betaorg.{PLATFORM_DOMAIN}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['primary_color'], '#ABCDEF')

    def test_org_without_custom_domain_resolves_via_subdomain(self):
        # org has no custom_domain — only subdomain route should work
        org = _make_branded_org(primary='#112233', slug='subonly')
        self.assertEqual(org.custom_domain, '')
        resp = self.client.get(THEME_URL, HTTP_HOST=f'subonly.{PLATFORM_DOMAIN}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['primary_color'], '#112233')
