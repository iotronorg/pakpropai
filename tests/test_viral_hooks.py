"""
Tests for FEATURE-VIRAL-ACQUISITION.

Coverage:
  a. VirtualHookInjector appends footer
  b. VirtualHookInjector idempotent (no double-append)
  c. VirtualHookInjector fail-open on DB error
  d. ReferralLinkGenerator.get_or_create is idempotent (stable link)
  e. ReferralLinkGenerator creates separate links for different leads
  f. PublicVerifyRedirectView 302 redirect + clicks incremented
  g. PublicVerifyRedirectView unknown ref → redirect to base
  h. PublicVerifyRedirectView missing ref → redirect to base
  i. PublicReferralConvertView creates stub lead + increments conversions
  j. PublicReferralConvertView invalid phone → 400
  k. PublicReferralConvertView invalid ref → 404
  l. PublicPlatformStatsView returns expected keys + 60s cache
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.campaigns.models import ReferralLink
from apps.campaigns.viral_hooks import ReferralLinkGenerator, VirtualHookInjector
from apps.leads.models import Lead

from tests.factories import make_developer, make_user


@override_settings(
    FRONTEND_URL='https://example.com',
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class ViralHooksTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        _, self.org = make_developer()
        self.org.is_active = True
        self.org.phone = '+923001234567'
        self.org.save(update_fields=['is_active', 'phone'])

    # ── a. Footer appended ───────────────────────────────────────────────────

    def test_inject_footer_appends_footer(self):
        result = VirtualHookInjector.inject_footer('Hello world', self.org)
        self.assertIn('✅ Verified by RealTron AI', result)
        self.assertIn('Hello world', result)
        self.assertTrue(result.startswith('Hello world'))

    # ── b. Idempotent ────────────────────────────────────────────────────────

    def test_inject_footer_idempotent(self):
        once  = VirtualHookInjector.inject_footer('Hello', self.org)
        twice = VirtualHookInjector.inject_footer(once, self.org)
        self.assertEqual(once, twice)

    # ── c. Fail-open ─────────────────────────────────────────────────────────

    def test_inject_footer_fail_open(self):
        with patch('apps.campaigns.viral_hooks.ReferralLinkGenerator.get_or_create',
                   side_effect=Exception('DB down')):
            result = VirtualHookInjector.inject_footer('Hello', self.org)
        self.assertEqual(result, 'Hello')

    # ── d. Idempotent link creation ──────────────────────────────────────────

    def test_referral_link_generator_idempotent(self):
        link1 = ReferralLinkGenerator.get_or_create(self.org)
        link2 = ReferralLinkGenerator.get_or_create(self.org)
        self.assertEqual(link1.pk, link2.pk)
        self.assertEqual(ReferralLink.objects.filter(org=self.org, lead__isnull=True).count(), 1)

    # ── e. Separate links per lead ───────────────────────────────────────────

    def test_referral_link_separate_per_lead(self):
        lead_user = make_user(phone='+923009990001')
        lead = Lead.objects.create(user=lead_user, organization=self.org)
        org_link  = ReferralLinkGenerator.get_or_create(self.org, lead=None)
        lead_link = ReferralLinkGenerator.get_or_create(self.org, lead=lead)
        self.assertNotEqual(org_link.pk, lead_link.pk)

    # ── f. Click tracking redirect ───────────────────────────────────────────

    def test_public_verify_redirect_increments_clicks(self):
        rl = ReferralLink.objects.create(org=self.org, code='testcode12345')
        resp = self.client.get('/public/verify/?ref=testcode12345')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('testcode12345', resp['Location'])
        self.assertIn(self.org.slug, resp['Location'])
        rl.refresh_from_db()
        self.assertEqual(rl.clicks, 1)

    # ── g. Unknown ref → base redirect ──────────────────────────────────────

    def test_public_verify_unknown_ref_redirects_to_base(self):
        resp = self.client.get('/public/verify/?ref=doesnotexist')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], 'https://example.com/public/verify')

    # ── h. Missing ref → base redirect ──────────────────────────────────────

    def test_public_verify_missing_ref_redirects_to_base(self):
        resp = self.client.get('/public/verify/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], 'https://example.com/public/verify')

    # ── i. Conversion creates lead + increments counter ──────────────────────

    def test_public_referral_convert_creates_lead(self):
        rl = ReferralLink.objects.create(org=self.org, code='convcode12345')
        resp = self.client.post('/public/referral/convert/', {
            'ref':   'convcode12345',
            'phone': '+923001111111',
        }, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertIn('org_whatsapp_number', resp.data)
        lead = Lead.objects.filter(organization=self.org, referral_code='convcode12345').first()
        self.assertIsNotNone(lead)
        self.assertEqual(lead.source, Lead.Source.REFERRAL_VIRAL)
        rl.refresh_from_db()
        self.assertEqual(rl.conversions, 1)

    # ── j. Invalid phone → 400 ───────────────────────────────────────────────

    def test_public_referral_convert_invalid_phone(self):
        ReferralLink.objects.create(org=self.org, code='phonetest1234')
        resp = self.client.post('/public/referral/convert/', {
            'ref':   'phonetest1234',
            'phone': '03001234567',  # missing +
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    # ── k. Invalid ref → 404 ─────────────────────────────────────────────────

    def test_public_referral_convert_invalid_ref(self):
        resp = self.client.post('/public/referral/convert/', {
            'ref':   'nonexistentref',
            'phone': '+923001234567',
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    # ── l. Platform stats keys + cache ──────────────────────────────────────

    def test_public_platform_stats_returns_expected_keys(self):
        from django.core.cache import cache
        cache.clear()
        resp = self.client.get('/public/platform-stats/')
        self.assertEqual(resp.status_code, 200)
        for key in ('total_verifications', 'scams_caught', 'active_orgs', 'leads_generated_today'):
            self.assertIn(key, resp.data)

    def test_public_platform_stats_cached(self):
        from django.core.cache import cache
        from apps.campaigns.public_views import _STATS_CACHE_KEY
        cache.clear()
        self.client.get('/public/platform-stats/')
        # Second call should hit cache — patch compute to prove it's not called
        with patch('apps.campaigns.public_views._compute_stats') as mock_compute:
            self.client.get('/public/platform-stats/')
            mock_compute.assert_not_called()
