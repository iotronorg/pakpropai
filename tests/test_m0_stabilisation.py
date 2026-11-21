"""
Milestone 0 stabilisation regressions:
  T-0008  portal scraping off by default; no false "live listings" promises
  T-0012  compare / market-trends routes reachable; numeric params validated
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.config.models import SystemConfig
from apps.config.services import SystemConfigService
from apps.properties.search import PropertySearchService, SearchParams
from tests.factories import make_client, make_developer, make_org, make_property


class ScraperDefaultTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_scraper_disabled_by_default(self):
        self.assertFalse(SystemConfigService.scraper_enabled())

    def test_scrapers_not_invoked_by_default(self):
        with patch('apps.properties.tasks.warm_search_cache.delay') as delay:
            PropertySearchService.search(city='Nowhere', phone='+923001112233')
        delay.assert_not_called()

    def test_no_live_pending_claim_when_disabled(self):
        out = PropertySearchService.execute(SearchParams(city='Nowhere'))
        self.assertFalse(out['live_search_pending'])
        self.assertNotIn('Zameen', out['whatsapp_message'])
        self.assertNotIn('being fetched', out['whatsapp_message'])

    def test_ai_tool_no_live_pending_when_disabled(self):
        from apps.ai import tools as tool_module
        from apps.ai._tools_search import search_properties
        tool_module.set_context(None, '+923001112233', org=None)
        result = search_properties(city='Nowhere')
        self.assertFalse(result['live_search_pending'])
        self.assertNotIn('Zameen', result['message'])

    def test_explicit_opt_in_still_works(self):
        SystemConfig.objects.update_or_create(key='scraper_search_enabled', defaults={'value': 'true'})
        cache.clear()
        self.assertTrue(SystemConfigService.scraper_enabled())


class PropertyRoutesTest(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()
        self.api = APIClient()
        self.api.force_authenticate(self.dev)

    def test_compare_route_reachable(self):
        a = make_property(org=self.org, title='A')
        b = make_property(org=self.org, title='B', location='Gulberg')
        resp = self.api.get(f'/api/v1/properties/compare/?ids={a.id},{b.id}')
        self.assertEqual(resp.status_code, 200, resp.content[:300])

    def test_compare_rejects_bad_uuid(self):
        resp = self.api.get('/api/v1/properties/compare/?ids=not-a-uuid')
        self.assertEqual(resp.status_code, 400)

    def test_compare_scoped_to_org(self):
        foreign = make_property(org=make_org(name='Other'), title='Foreign')
        resp = self.api.get(f'/api/v1/properties/compare/?ids={foreign.id}')
        self.assertNotIn('Foreign', resp.content.decode())

    def test_market_trends_route_reachable(self):
        make_property(org=self.org)
        resp = self.api.get('/api/v1/properties/market-trends/?city=Lahore')
        self.assertEqual(resp.status_code, 200, resp.content[:300])

    def test_detail_route_still_works(self):
        prop = make_property(org=self.org)
        resp = self.api.get(f'/api/v1/properties/{prop.id}/')
        self.assertEqual(resp.status_code, 200)


class PropertyParamValidationTest(TestCase):

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(make_client())

    def test_non_numeric_price_is_400(self):
        for param in ('min_price=abc', 'max_price=1e9x', 'min_score=high'):
            resp = self.api.get(f'/api/v1/properties/?{param}')
            self.assertEqual(resp.status_code, 400, param)

    def test_negative_is_400(self):
        resp = self.api.get('/api/v1/properties/?min_price=-5')
        self.assertEqual(resp.status_code, 400)

    def test_valid_numbers_filter(self):
        make_property(org=make_org(name='P', operational_mode='production'), title='cheap', price=1_000_000)
        make_property(org=make_org(name='Q', operational_mode='production'), title='dear',
                      price=9_000_000, location='Gulberg')
        resp = self.api.get('/api/v1/properties/?max_price=2,000,000')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({r['title'] for r in resp.json()['results']}, {'cheap'})


class FrozenModulesTest(TestCase):
    """T-0015: voice calling and syndication are frozen by default."""

    def setUp(self):
        self.dev, self.org = make_developer()
        self.api = APIClient()
        self.api.force_authenticate(self.dev)

    def test_outbound_call_blocked(self):
        resp = self.api.post('/api/v1/voice/calls/initiate/', {'lead_id': 'x'})
        self.assertEqual(resp.status_code, 403)

    @patch('apps.voice.views.VoiceInboundWebhookView._validate_signature', return_value=True)
    def test_inbound_call_politely_hung_up(self, _sig):
        from apps.voice.models import VoiceCallSession
        resp = APIClient().post('/api/v1/voice/webhook/inbound/', {
            'CallSid': 'CA123', 'From': '+15551110000', 'To': '+15550000001',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'<Hangup/>', resp.content)
        self.assertFalse(VoiceCallSession.objects.filter(call_sid='CA123').exists())

    @patch('apps.voice.views.VoiceInboundWebhookView._validate_signature', return_value=False)
    def test_inbound_signature_still_checked_first(self, _sig):
        resp = APIClient().post('/api/v1/voice/webhook/inbound/', {'CallSid': 'CA9'})
        self.assertEqual(resp.status_code, 403)

    def test_voice_history_still_readable(self):
        self.assertEqual(self.api.get('/api/v1/voice/calls/').status_code, 200)

    def test_syndication_create_and_browse_blocked(self):
        self.assertEqual(self.api.post('/api/v1/marketplace/listings/', {}).status_code, 403)
        self.assertEqual(self.api.post('/api/v1/marketplace/submissions/', {}).status_code, 403)
        agent_api = APIClient()
        from tests.factories import make_agent
        agent_user, _ = make_agent(org=self.org)
        agent_api.force_authenticate(agent_user)
        self.assertEqual(agent_api.get('/api/v1/marketplace/browse/').status_code, 403)

    def test_syndication_existing_records_readable(self):
        self.assertEqual(self.api.get('/api/v1/marketplace/listings/').status_code, 200)
        self.assertEqual(self.api.get('/api/v1/marketplace/ledger/').status_code, 200)
