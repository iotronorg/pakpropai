"""T-2030 — SearchCriteria, listing search v2, ranking, search tools and POST /search/ (ADR-034)."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai import _tools_listings as T
from apps.ai import grounding as G
from apps.ai._tools_context import set_context
from apps.ai.service import IntentClassifier
from apps.conversations import store
from apps.conversations.models import ConversationState, Message
from apps.locations.models import Location
from apps.markets.models import Market
from apps.properties import criteria as C
from apps.properties import listing_search as S
from apps.properties.models import Amenity, Property
from tests.factories import make_client, make_org, make_property
from tests.test_runtime import LOCMEM, Scripted, calls, final

URL = '/api/v1/search/'
PHONE = '923001235500'


@override_settings(CACHES=LOCMEM)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.org = make_org(operational_mode='production')

    def make(self, title, **kw):
        kw = {'price': 10_000_000, 'size_value': 10, 'size_unit': 'marla', 'bedrooms': 3, **kw}
        extra = {k: kw.pop(k) for k in ('is_sandbox', 'created_at', 'published_at', 'status', 'is_active', 'geo_lat',
                                        'geo_lng', 'expires_at', 'legal_status') if k in kw}
        amenities = kw.pop('amenities', ())
        p = make_property(org=self.org, title=title, **kw)
        for k in amenities:
            p.amenities.add(Amenity.objects.get_or_create(key=k, defaults={'group': 'building', 'labels': {}})[0])
        if extra:
            Property.objects.filter(pk=p.pk).update(**extra)
        return p

    def node(self, name, level, parent=None, **kw):
        existing = Location.objects.filter(market_id='PK', level=level, name=name, parent=parent).first()
        return existing or Location.objects.create(market_id='PK', name=name, level=level, parent=parent, **kw)

    def run_search(self, data=None, **kw):
        crit, _ = C.prepare(C.parse(data or {}))
        return S.execute(crit, **kw)

    def titles(self, data=None, **kw):
        return [h.listing.title for h in self.run_search(data, **kw).hits]

    def cities(self):
        pk = Location.objects.filter(market_id='PK', level='country').first() or self.node('Pakistan', 'country')
        out = []
        for name in ('Testabad', 'Otherpur'):
            out.append(self.node(name, 'city', pk))
        return out


class CriteriaTest(Base):
    def errors(self, data):
        with self.assertRaises(C.CriteriaInvalid) as e:
            C.prepare(C.parse(data))
        return e.exception.errors

    def test_field_errors(self):
        for data, key in (({'nope': 1}, 'nope'), ({'category': 'castle'}, 'category'),
                          ({'subtypes': ['castle']}, 'subtypes'), ({'market': 'ZZ'}, 'market'),
                          ({'category': 'land', 'subtypes': ['villa']}, 'subtypes'),
                          ({'price': {'min': 5, 'max': 4}}, 'price'), ({'price': {'max': 10 ** 14}}, 'price.max'),
                          ({'price': {'max': 5, 'currency': 'XXX'}}, 'price.currency'),
                          ({'size': {'max': 5, 'unit': 'bigha'}}, 'size.unit'),
                          ({'size': {'max': float('nan')}}, 'size.max'),
                          ({'location': {'location_ids': [10 ** 9]}}, 'location.location_ids'),
                          ({'location': {'geo': {'type': 'radius', 'lat': 1}}}, 'location.geo'),
                          ({'bedrooms': {'min': 99}}, 'bedrooms.min')):
            self.assertIn(key, self.errors(data), data)

    def test_inactive_market_is_refused(self):
        Market.objects.filter(code='GB').update(status='inactive')
        self.assertEqual(self.errors({'market': 'gb'}), {'market': ['market.unsupported']})

    def test_defaults_are_reported_as_assumed(self):
        c, assumed = C.prepare(C.parse({'price': {'max': 5}, 'size': {'max': 5}, 'purpose': 'rent_long'}))
        self.assertEqual((c.price.currency, c.price.period, c.size.unit), ('PKR', 'month', 'marla'))
        self.assertEqual(assumed, ['price.currency', 'price.period', 'size.unit'])
        self.assertEqual(C.prepare(C.parse({'price': {'max': 5, 'currency': 'pkr', 'period': 'total'}}))[1], [])

    def test_missing_and_unknown_amenities(self):
        c, _ = C.prepare(C.parse({'amenities_required': ['Helipad', 'gym']}))
        self.assertEqual(c.missing_required, ['location', 'purpose'])
        self.assertEqual((c.amenities_required, c.soft_preferences.notes), (['gym'], 'helipad'))

    def test_policy_text_never_filters(self):
        c, _ = C.prepare(C.parse({'keywords': 'safe family-friendly corner', 'soft_preferences': {
            'style': ['modern', 'muslim neighbourhood']}, 'location': {'free_text': 'near the metro Zzplace'}}))
        self.assertEqual((c.keywords, c.soft_preferences.style, c.location.free_text), ('corner', ['modern'], 'Zzplace'))
        self.assertEqual({u.reason for u in c.unsupported_requests}, {'subjective_or_protected', 'no_data'})

    def test_places(self):
        a, b = self.cities()
        town_a, town_b = self.node('Greenville', 'locality', a), self.node('Greenville', 'locality', b)
        phase = self.node('Phase 5', 'sub_locality', town_a)
        for text, want in (('Testabad', (a, '')), ('Greenville, Testabad', (town_a, '')),
                           ('Greenville Otherpur', (town_b, '')), ('Greenville Phase-5, Testabad', (phase, '')),
                           ('Block Q, Testabad', (a, 'Block Q')), ('Nowhere', (None, 'Nowhere'))):
            self.assertEqual(C.resolve_place('PK', text), want, text)
        with self.assertRaises(C.LocationAmbiguous) as e:
            C.resolve_place('PK', 'greenville')
        self.assertEqual({x['label'] for x in e.exception.candidates},
                         {'Greenville, Testabad', 'Greenville, Otherpur'})


class FilterTest(Base):
    def test_visibility(self):
        self.make('ok')
        for i, kw in enumerate(({'is_sandbox': True}, {'status': 'suspended', 'is_active': False},
                                {'expires_at': timezone.now() - timedelta(days=1)})):
            self.make(f'hidden{i}', **kw)
        make_property(org=self.org, title='abroad', country='AE', city='Dubai')
        self.assertEqual(self.titles(), ['ok'])

    def test_own_sandbox_only_for_the_viewer_org(self):
        self.make('sbx', is_sandbox=True)
        self.assertEqual(self.titles(), [])
        self.assertEqual(self.titles(viewer_org=self.org), ['sbx'])
        self.assertEqual(self.titles(viewer_org=make_org(name='x')), [])

    def test_basic_filters(self):
        self.make('a', bedrooms=2, bathrooms=1, amenities=['gym'], furnished_status='furnished', subtype='house')
        self.make('b', bedrooms=4, bathrooms=3, amenities=['gym', 'lift'], construction_status='ready',
                  pets_allowed=True, legal_status='verified', subtype='villa', installment_available=True)
        self.make('c', bedrooms=None, category='land', created_at=timezone.now() - timedelta(days=40))
        for data, want in (({'bedrooms': {'min': 3}}, 'b'), ({'bedrooms': {'max': 3}}, 'a'),
                           ({'bathrooms': {'min': 2}}, 'b'), ({'amenities_required': ['gym', 'lift']}, 'b'),
                           ({'amenities_required': ['gym']}, 'ab'), ({'furnishing': 'furnished'}, 'a'),
                           ({'completion': 'ready'}, 'b'), ({'pets_allowed': True}, 'b'), ({'verified_only': True}, 'b'),
                           ({'subtypes': ['house', 'villa']}, 'ab'), ({'category': 'land'}, 'c'),
                           ({'installments': True}, 'b')):
            self.assertEqual(sorted(self.titles(data)), list(want), data)

    def test_listed_within_days(self):
        self.make('new')
        old = self.make('old')
        Property.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40), published_at=None)
        self.assertEqual(self.titles({'listed_within_days': 7}), ['new'])

    def test_price_period_currency_and_hidden_prices(self):
        self.make('sale', price=10_000_000)
        self.make('hidden', price=9_000_000, price_on_request=True)
        self.make('usd', price=100, currency='USD')
        self.make('month', purpose='rent_long', price_type='per_month', price=100_000)
        self.make('year', purpose='rent_long', price_type='per_year', price=1_200_000)
        self.assertEqual(self.titles({'purpose': 'sale', 'price': {'max': 10_000_000}}), ['sale'])
        rent = lambda **p: sorted(self.titles({'purpose': 'rent_long', 'price': p}))     # noqa: E731
        self.assertEqual(rent(max=100_000), ['month', 'year'])
        self.assertEqual(rent(max=99_999), [])
        self.assertEqual(rent(max=1_200_000, period='year'), ['month', 'year'])
        self.assertEqual(rent(min=1_200_001, period='year'), [])

    def test_size_units(self):
        self.make('kanal', size_value=1, size_unit='kanal')
        self.make('marla', size_value=10)
        self.assertEqual(self.titles({'size': {'min': 20, 'max': 20, 'unit': 'marla'}}), ['kanal'])

    def test_location_tree_and_text_fallback(self):
        a, b = self.cities()
        town, phase = self.node('Greenville', 'locality', a), None
        phase = self.node('Phase 5', 'sub_locality', town)
        self.make('node', city='Testabad', location='anything', location_ref=phase)
        self.make('text', city='Testabad', location='Greenville Block C')
        self.make('elsewhere', city='Otherpur', location='Greenville')
        self.make('plain', city='Testabad', location='Canal Road')
        q = lambda t: sorted(self.titles({'location': {'free_text': t}}))       # noqa: E731
        self.assertEqual(q('Greenville, Testabad'), ['node', 'text'])
        self.assertEqual(q('Testabad'), ['node', 'plain', 'text'])
        self.assertEqual(q('Canal Road, Testabad'), ['plain'])
        self.assertEqual(self.titles({'location': {'location_ids': [phase.pk]}}), ['node'])
        self.assertEqual(self.titles({'location': {'free_text': 'canal'}}), ['plain'])

    def test_geo(self):
        self.make('near', latitude='31.5000', longitude='74.3000', location_precision='exact')
        self.make('far', latitude='31.9000', longitude='74.3000', location_precision='exact')
        g = {'type': 'radius', 'lat': 31.5, 'lng': 74.3, 'radius_m': 5000}
        self.assertEqual(self.titles({'location': {'geo': g}}), ['near'])
        self.assertIn('0.0 km from the point you chose', self.run_search({'location': {'geo': g}}).hits[0].reasons)
        box = {'type': 'bbox', 'min_lng': 74, 'min_lat': 31.8, 'max_lng': 75, 'max_lat': 32}
        self.assertEqual(self.titles({'location': {'geo': box}}), ['far'])

    def test_keywords(self):
        self.make('Corner villa')
        self.make('Other')
        self.assertEqual(self.titles({'keywords': "corner & ' | !"}), ['Corner villa'])


class RankingTest(Base):
    def test_preferred_amenities_and_style_raise_the_score(self):
        self.make('plain')
        self.make('gym', amenities=['gym'])
        self.make('modern one')
        self.assertEqual(self.titles({'amenities_preferred': ['gym']})[0], 'gym')
        self.assertEqual(self.titles({'soft_preferences': {'style': ['modern']}})[0], 'modern one')

    def test_verified_disputed_and_fresh(self):
        self.make('disputed', legal_status='disputed')
        self.make('verified', legal_status='verified')
        self.make('stale', legal_status='disputed', published_at=timezone.now() - timedelta(days=80))
        self.assertEqual(self.titles(), ['verified', 'disputed', 'stale'])

    def test_reasons_are_listing_facts(self):
        self.make('x', price=9_900_000, bedrooms=4, amenities=['gym'])
        h = self.run_search({'purpose': 'sale', 'price': {'max': 10_000_000}, 'bedrooms': {'min': 3},
                             'amenities_required': ['gym'], 'amenities_preferred': ['lift']}).hits[0]
        self.assertEqual(h.reasons[0], '4 bedrooms (you asked for 3+)')
        self.assertTrue(any(r.lower() == 'gym listed in amenities' for r in h.reasons))
        self.assertEqual((len(h.reasons), len(h.tradeoffs)), (3, 1))
        self.assertEqual(h.tradeoffs, ['Near the top of your budget'])
        self.assertTrue(0 <= h.score <= 100)

    def test_market_weights(self):
        self.make('cheap', price=1_000_000, legal_status='disputed')
        self.make('verified', price=10_000_000, legal_status='verified')
        data = {'purpose': 'sale', 'price': {'max': 10_000_000}}
        self.assertEqual(self.titles(data)[0], 'cheap')
        Market.objects.filter(code='PK').update(settings={'ranking_weights': {'verification': 100, 'price': 0, 'x': 9,
                                                                              'quality': 'junk', 'size': True}})
        self.assertEqual(self.titles(data)[0], 'verified')
        self.assertTrue(self.run_search(data).ranking_version.startswith('v1-PK-'))
        Market.objects.filter(code='PK').update(settings={'ranking_weights': {k: 0 for k in S.WEIGHTS}})
        self.assertEqual(S.weights_for('PK'), (S.WEIGHTS, 'v1'))

    def test_price_sort_does_not_order_hidden_prices(self):
        self.make('b', price=20)
        self.make('a', price=10)
        self.make('hidden', price=1, price_on_request=True)
        self.assertEqual(self.titles(sort='price_asc'), ['a', 'b', 'hidden'])
        self.assertEqual(self.titles(sort='price_desc'), ['b', 'a', 'hidden'])

    def test_cursor_walks_everything_once(self):
        for i in range(7):
            self.make(f'p{i}', price=1000 + i // 2)
        for sort in S.SORTS:
            seen, cursor = [], ''
            while cursor is not None:
                r = self.run_search(limit=3, cursor=cursor, sort=sort)
                seen += [h.listing.title for h in r.hits]
                cursor = r.next_cursor
            self.assertEqual((len(seen), len(set(seen))), (7, 7), sort)
            self.assertEqual(seen, self.titles(sort=sort), sort)

    def test_cursor_is_bound_to_the_search(self):
        for i in range(3):
            self.make(f'p{i}')
        token = self.run_search(limit=1).next_cursor
        for kw in ({'sort': 'newest'}, {'viewer_org': self.org}):
            with self.assertRaises(S.CursorInvalid):
                self.run_search(cursor=token, **kw)
        with self.assertRaises(S.CursorInvalid):
            self.run_search({'bedrooms': {'min': 1}}, cursor=token)
        with self.assertRaises(S.CursorInvalid):
            self.run_search(cursor=token[:-2] + 'xx')

    def test_relaxations_and_facets(self):
        self.make('x', price=20_000_000, bedrooms=3, amenities=['gym'])
        r = self.run_search({'purpose': 'sale', 'price': {'max': 10_000_000}, 'bedrooms': {'min': 2}})
        self.assertEqual((r.total, r.relaxations), (0, [{'drop': 'price', 'count': 1}]))
        f = self.run_search(with_facets=True).facets
        self.assertEqual((f['bedrooms'], f['amenities'], f['purpose']), ({'3': 1}, {'gym': 1}, {'sale': 1}))

    def test_query_count_does_not_grow(self):
        counts = []
        for n in (2, 6):
            for i in range(n):
                self.make(f'{n}-{i}', amenities=['gym'])
            crit, _ = C.prepare(C.parse({'amenities_preferred': ['gym']}))
            with CaptureQueriesContext(connection, ) as q:
                S.execute(crit)
            counts.append(len(q))
        self.assertEqual(counts[0], counts[1])


class RestTest(Base):
    def post(self, data, **kw):
        return APIClient().post(URL, data, format='json', **kw)

    def test_public_search(self):
        p = self.make('x', price_on_request=True)
        r = self.post({'facets': True, 'limit': 5})
        self.assertEqual(r.status_code, 200)
        hit = r.data['results'][0]
        self.assertEqual((hit['listing']['id'], hit['listing']['price'], hit['sponsored']), (str(p.pk), None, False))
        for private in ('owner', 'phone', 'latitude', 'listed_by_agent', 'organization'):
            self.assertNotIn(private, hit['listing'])

    def test_errors(self):
        for data, key in (({'sort': 'x'}, 'sort'), ({'limit': 51}, 'limit'), ({'limit': True}, 'limit'),
                          ({'cursor': 'junk'}, 'cursor'), ({'facets': 'x'}, 'facets'), ({'nope': 1}, 'nope'),
                          ({'price': {'max': 5, 'currency': 'XXX'}}, 'price.currency')):
            r = self.post(data)
            self.assertEqual((r.status_code, key in r.data), (400, True), data)
        self.assertEqual(self.post([1]).status_code, 400)

    def test_array_body_is_still_scanned(self):      # KI-067
        self.assertEqual(self.post(["1' OR '1'='1' UNION SELECT password FROM users --"]).status_code, 403)

    def test_ambiguous_location(self):
        a, b = self.cities()
        node = self.node('Greenville', 'locality', a)
        self.node('Greenville', 'locality', b)
        r = self.post({'location': {'free_text': 'Greenville'}})
        self.assertEqual((r.status_code, r.data['code'], len(r.data['candidates'])), (422, 'location.ambiguous', 2))
        self.assertEqual(self.post({'location': {'location_ids': [node.pk]}}).status_code, 200)

    def test_throttled(self):
        with patch('apps.properties.search_views.PropertySearchThrottle.get_rate', return_value='1/min'):
            self.assertEqual([self.post({}).status_code, self.post({}).status_code], [200, 429])


class ToolTest(Base):
    def setUp(self):
        super().setUp()
        self.user = make_client(phone='+' + PHONE)
        self.conv = store.open_whatsapp(PHONE, self.user)
        self.ctx()

    def ctx(self, message='', org=None):
        set_context(self.user, PHONE, org=org, message=message)

    def save(self, result):
        ConversationState.objects.update_or_create(conversation=self.conv, defaults={
            'active_criteria': result['criteria'], 'last_results': [p['id'] for p in result['properties']]})
        return result

    def test_same_results_as_rest(self):
        for i in range(4):
            self.make(f'p{i}', price=5_000_000 + i, bedrooms=i + 1)
        tool = T.search_listings(location='Lahore', purpose='buy', max_price=9_000_000, currency='PKR', min_bedrooms=2)
        rest = APIClient().post(URL, tool['criteria'], format='json')
        self.assertEqual([p['id'] for p in tool['properties']], [r['listing']['id'] for r in rest.data['results']])
        self.assertEqual([p['match_score'] for p in tool['properties']], [r['match_score'] for r in rest.data['results']])

    def test_clarification_at_most_two_and_purpose_once(self):
        self.make('x')
        first = self.save(T.search_listings(property_type='house'))
        self.assertEqual(first['questions'], ['Which city or area?', 'Are you buying or renting?'])
        self.assertEqual(first['properties'], [])
        second = self.save(T.update_session_criteria(location='Lahore'))      # still no purpose: do not ask again
        self.assertNotIn('needs_clarification', second)
        self.assertIn('needs_clarification', T.search_listings(location=''))
        self.assertEqual(T.search_listings(location='Lahore', purpose='any')['count'], 1)

    def test_ambiguous_place_is_asked(self):
        for c in self.cities():
            self.node('Greenville', 'locality', c)
        r = T.search_listings(location='Greenville', purpose='buy')
        self.assertIn('Greenville, Testabad', r['questions'][0])

    def test_refinement_keeps_and_clears(self):
        self.make('cheap', price=4_000_000, bedrooms=2)
        self.make('dear', price=9_000_000, bedrooms=4)
        self.assertEqual(self.save(T.search_listings(location='Lahore', purpose='buy', min_bedrooms=3))['count'], 1)
        self.assertEqual(self.save(T.update_session_criteria(max_price=5_000_000))['count'], 0)
        r = self.save(T.update_session_criteria(clear='bedrooms'))
        self.assertEqual([p['title'] for p in r['properties']], ['cheap'])
        r = T.update_session_criteria(min_price=6_000_000)          # contradicts the old maximum: replaces it
        self.assertEqual(r['criteria']['price']['min'], 6_000_000)
        self.assertNotIn('max', r['criteria']['price'])

    def test_stored_junk_is_ignored(self):
        ConversationState.objects.create(conversation=self.conv, active_criteria={'city': 'Lahore', 'amenities': 'x'})
        self.make('x')
        self.assertEqual(T.update_session_criteria(location='Lahore', purpose='buy')['count'], 1)

    def test_assumed_unsupported_and_relaxations(self):
        self.make('x', price=20_000_000)
        self.ctx('a house in a safe family-friendly area near the metro')
        r = T.search_listings(location='Lahore', purpose='buy', max_price=5_000_000)
        self.assertEqual(r['assumed'], {'price.currency': 'PKR'})
        self.assertEqual(len(r['unsupported']), 3)
        self.assertEqual(r['relaxations'], [{'drop': 'price', 'count': 1}])

    def test_invalid_input_is_an_error_not_a_crash(self):
        self.assertIn('Invalid search', T.search_listings(location='Lahore', purpose='buy', min_bedrooms=99)['error'])
        with patch('apps.properties.listing_search.execute', side_effect=RuntimeError('secret')):
            r = T.search_listings(location='Lahore', purpose='buy')
        self.assertNotIn('secret', str(r))
        self.assertNotIn('properties', r)

    def test_sandbox_of_another_org_is_never_returned(self):
        p = self.make('sbx', is_sandbox=True)
        self.ctx(org=make_org(name='other'))
        self.assertEqual(T.search_listings(location='Lahore', purpose='buy')['count'], 0)
        self.assertFalse(T.get_listing(str(p.pk))['found'])

    def test_get_listing(self):
        p = self.make('x', price=5, price_on_request=True, deposit=7, owner=None)
        self.save(T.search_listings(location='Lahore', purpose='buy'))
        for ref in (str(p.pk), p.ref_no.lower(), '1', '#1'):
            r = T.get_listing(ref)
            self.assertTrue(r['found'], ref)
        row = r['properties'][0]
        self.assertEqual((row['price'], row['price_formatted'], 'deposit' in row), (0, 'Price on request', False))
        for private in ('owner', 'phone', 'latitude', 'raw_docs', 'ai_analysis'):
            self.assertNotIn(private, row)
        for ref in ('2', '0', '', 'nonsense'):
            self.assertFalse(T.get_listing(ref)['found'], ref)

    def test_recorded_deposit_may_be_stated(self):
        p = self.make('Corner House', price=100_000, purpose='rent_long', price_type='per_month', deposit=300_000)
        e = G.Evidence('deposit?', [], '')
        e.add(T.get_listing(str(p.pk)))
        self.assertTrue(G.check('Corner House: deposit PKR 300,000', e).ok)
        self.assertFalse(G.check('Corner House: deposit PKR 400,000', e).ok)


class EndToEndTest(Base):
    def test_state_matches_and_detail_call_keeps_the_search(self):
        from apps.ai.schemas import IntentResult
        from apps.ai.service import AIServiceManager
        user = make_client(phone='+' + PHONE)
        p = self.make('Corner House Gulberg', price=9_000_000)
        b = Scripted('gemini', [
            calls(('search_listings', {'location': 'Lahore', 'purpose': 'buy', 'max_price': 10_000_000})),
            final('Corner House Gulberg: PKR 9,000,000, 10 marla'),
            calls(('get_listing', {'listing': '1'})), final('Corner House Gulberg has 3 bedrooms'),
            calls(('update_session_criteria', {'max_price': 5_000_000})), final('Nothing at that price.')])
        with override_settings(AI_PROVIDER_ORDER=['gemini']), patch('apps.ai.llm.make_backend', return_value=b), \
                patch('apps.ai.context.DynamicContextBuilder.build', return_value=''), \
                patch('apps.ai.service.IntentClassifier.classify',
                      return_value=IntentResult(intent='general_query', confidence=0.5)):
            svc = AIServiceManager()
            svc.process(PHONE, 'houses to buy in Lahore', user)
            m = Message.objects.filter(role='assistant').latest('seq')
            self.assertEqual(m.matches[str(p.pk)]['reasons'][0], 'Within your budget (PKR 9,000,000)')
            svc.process(PHONE, 'how many bedrooms in the first', user)
            state = ConversationState.objects.get()
            self.assertEqual((state.last_results, state.active_criteria['price']['max']), ([str(p.pk)], 10_000_000))
            svc.process(PHONE, 'something cheaper', user)
        state.refresh_from_db()
        self.assertEqual((state.last_results, state.active_criteria['price']['max'], state.active_criteria['purpose']),
                         ([], 5_000_000, 'sale'))
        from apps.conversations.views import cards
        c = cards([str(p.pk)], None, m.matches)[0]
        self.assertTrue(c['reasons'] and c['match_score'] is not None)


class ClassifierTest(SimpleTestCase):
    def test_purpose_is_not_guessed(self):
        f = lambda t: IntentClassifier._extract_search_filter(t, t.lower(), '').purpose     # noqa: E731
        self.assertEqual([f('5 marla house'), f('house for rent'), f('want to buy a house')], ['', 'rent', 'buy'])

    def test_unknown_city_is_not_sent(self):
        sf = IntentClassifier._extract_search_filter('5 marla house', '5 marla house', '')
        self.assertNotIn('Unknown', IntentClassifier._normalise_search_query(sf))
