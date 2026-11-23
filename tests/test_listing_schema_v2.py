"""
T-1070a — listing schema v2, expand phase (ADR-023).

Purpose / rooms / floors / price terms on Property, their API surface,
search filters, WhatsApp formatting and the AI search path.
"""
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.properties.models import Property
from apps.properties.scrapers.base import PropertyResult
from apps.properties.search import PropertySearchService, SearchParams, purpose_values
from tests.factories import make_developer, make_property

URL = '/api/v1/properties/'


class FieldDefaultsTest(TestCase):
    def test_new_listing_defaults(self):
        _, org = make_developer()
        p = make_property(org=org)
        p.refresh_from_db()
        self.assertEqual((p.purpose, p.price_type), ('sale', 'total'))
        self.assertEqual((p.price_negotiable, p.price_on_request, p.attributes), (False, False, {}))
        self.assertIsNone(p.bedrooms)

    def test_db_defaults_cover_pre_v2_inserts(self):
        """A rolling deploy's old code inserts without the v2 columns: the DB fills them."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name = 'properties' AND column_name = ANY(%s)",
                [['purpose', 'price_type', 'price_negotiable', 'price_on_request', 'attributes']])
            defaults = dict(cur.fetchall())
        self.assertEqual(len(defaults), 5)
        self.assertTrue(all(defaults.values()), defaults)


class CrossFieldRulesTest(SimpleTestCase):
    def test_rules(self):
        e = Property.v2_errors
        self.assertEqual(e({}), {})
        self.assertIn('price_type', e({'purpose': 'sale', 'price_type': 'per_month'}))
        self.assertEqual(e({'purpose': 'rent_long', 'price_type': 'per_month'}), {})
        self.assertEqual(e({'purpose': 'sale', 'price_type': 'per_sqm'}), {})
        self.assertIn('floor', e({'floor': 9, 'total_floors': 4}))
        self.assertEqual(e({'floor': -1, 'total_floors': 4}), {})
        self.assertIn('year_built', e({'year_built': 1700}))
        self.assertIn('deposit', e({'deposit': -5}))
        self.assertIn('attributes', e({'attributes': ['x']}))
        self.assertIn('attributes', e({'attributes': {f'k{i}': i for i in range(51)}}))
        self.assertIn('attributes', e({'attributes': {'k': 'x' * 9000}}))
        self.assertEqual(e({'attributes': {'possession': True}}), {})


class ApiTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)

    def test_create_and_read_v2_fields(self):
        r = self.c.post(URL, {
            'title': 'Flat', 'city': 'Lahore', 'location': 'Gulberg', 'price': 150_000,
            'purpose': 'rent_long', 'price_type': 'per_month', 'bedrooms': 3, 'bathrooms': 2,
            'floor': 4, 'total_floors': 10, 'year_built': 2015, 'parking_spaces': 1,
            'pets_allowed': False, 'deposit': 300_000, 'price_negotiable': True,
            'attributes': {'possession': 'available'},   # valid under the PK schema (T-1070e),
        }, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        p = Property.objects.get(title='Flat')
        self.assertEqual((p.purpose, p.price_type, p.bedrooms, p.deposit), ('rent_long', 'per_month', 3, 300_000))
        d = self.c.get(f'{URL}{p.pk}/').data
        self.assertEqual((d['purpose'], d['bedrooms'], d['attributes']), ('rent_long', 3, {'possession': 'available'}))
        row = self.c.get(URL).data['results'][0]
        self.assertEqual((row['purpose'], row['price_type'], row['bathrooms']), ('rent_long', 'per_month', 2))

    def test_create_rejects_periodic_sale_price(self):
        r = self.c.post(URL, {'title': 'X', 'city': 'Lahore', 'location': 'DHA', 'price': 1,
                              'price_type': 'per_month'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('price_type', r.data)

    def test_partial_update_validates_against_stored_values(self):
        p = make_property(org=self.org, owner=self.dev)       # IsOwnerOrReadOnly
        r = self.c.patch(f'{URL}{p.pk}/', {'price_type': 'per_month'}, format='json')
        self.assertEqual(r.status_code, 400)                    # stored purpose is sale
        r = self.c.patch(f'{URL}{p.pk}/', {'purpose': 'rent_long', 'price_type': 'per_month'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        r = self.c.patch(f'{URL}{p.pk}/', {'purpose': 'sale'}, format='json')
        self.assertEqual(r.status_code, 400)                    # stored price_type is per_month

    def test_list_filters(self):
        make_property(org=self.org, title='Sale 2b', bedrooms=2)
        make_property(org=self.org, title='Rent 3b', purpose='rent_long', price_type='per_month', bedrooms=3)
        make_property(org=self.org, title='Rent 1b', purpose='rent_short', price_type='per_month', bedrooms=1)

        def titles(q):
            return sorted(x['title'] for x in self.c.get(URL + q).data['results'])

        self.assertEqual(titles('?purpose=rent_long'), ['Rent 3b'])
        self.assertEqual(titles('?min_bedrooms=2'), ['Rent 3b', 'Sale 2b'])
        self.assertEqual(titles('?purpose=sale&min_bedrooms=3'), [])
        self.assertEqual(self.c.get(URL + '?purpose=rent').status_code, 400)
        self.assertEqual(self.c.get(URL + '?min_bedrooms=x').status_code, 400)


class SearchTest(TestCase):
    def setUp(self):
        _, self.org = make_developer()
        # distinct prices/locations: identical rows would be merged by _dedup
        make_property(org=self.org, title='Sale house', bedrooms=4, location='DHA', price=9_000_000)
        make_property(org=self.org, title='Long rent', purpose='rent_long', price_type='per_month',
                      bedrooms=2, location='Gulberg', price=120_000)
        make_property(org=self.org, title='Holiday let', purpose='rent_short', price_type='per_month',
                      bedrooms=3, location='Johar Town', price=80_000)

    def _titles(self, **kw):
        return sorted(r.title for r in PropertySearchService._from_db(city='Lahore', viewer_org=self.org, **kw))

    def test_purpose_aliases(self):
        self.assertEqual(purpose_values('buy'), ('sale',))
        self.assertEqual(purpose_values('Rent'), ('rent_long', 'rent_short'))
        self.assertEqual(purpose_values('rent_short'), ('rent_short',))
        self.assertEqual(purpose_values('lease'), ('lease_commercial',))
        self.assertEqual(purpose_values('whatever'), ())

    def test_db_filters(self):
        self.assertEqual(self._titles(purpose='rent'), ['Holiday let', 'Long rent'])
        self.assertEqual(self._titles(purpose='buy'), ['Sale house'])
        self.assertEqual(self._titles(min_bedrooms=3), ['Holiday let', 'Sale house'])
        self.assertEqual(len(self._titles()), 3)

    def test_rent_search_skips_sale_only_scrapers(self):
        with patch.object(PropertySearchService, '_from_scrapers', return_value=[]) as scr, \
                patch('apps.config.services.SystemConfigService.scraper_enabled', return_value=True):
            out = PropertySearchService.execute(
                SearchParams(city='Lahore', purpose='rent', viewer_org_id=str(self.org.pk)))
            scr.assert_not_called()
            self.assertFalse(out['live_search_pending'])
            self.assertEqual({r['purpose'] for r in out['results']}, {'rent_long', 'rent_short'})
            PropertySearchService.execute(SearchParams(city='Lahore', purpose='buy'))
            scr.assert_called_once()

    def test_from_dict_reads_room_params(self):
        p = SearchParams.from_dict({'bedrooms': 3, 'min_bathrooms': '2', 'purpose': 'rent'})
        self.assertEqual((p.min_bedrooms, p.min_bathrooms, p.purpose), (3, 2, 'rent'))


class WhatsAppFormatTest(SimpleTestCase):
    def test_rent_listing_line(self):
        line = PropertyResult(source='pakprop', source_id='1', title='Flat', city='Lahore',
                              location='Gulberg', price=150_000, currency='PKR',
                              purpose='rent_long', price_type='per_month', bedrooms=3).format_wa(1)
        self.assertIn('/month', line)
        self.assertIn('3 bed', line)
        self.assertIn('for rent', line)

    def test_price_on_request(self):
        line = PropertyResult(source='pakprop', source_id='1', title='Villa', city='Lahore',
                              location='DHA', price=None, price_on_request=True).format_wa()
        self.assertIn('Price on request', line)

    def test_cached_scraper_dicts_still_load(self):
        old = {'source': 'zameen', 'source_id': 'z1', 'title': 't', 'city': 'c', 'location': 'l'}
        r = PropertyResult.from_dict(old)
        self.assertEqual((r.purpose, r.bedrooms), ('sale', None))


class AiSearchPathTest(TestCase):
    def test_local_extractor_reads_bedrooms_and_purpose(self):
        from apps.ai.service import IntentClassifier
        msg = '3 bed house for rent in DHA Lahore'
        sf = IntentClassifier._extract_search_filter(msg, msg.lower(), 'Lahore')
        self.assertEqual((sf.bedrooms, sf.purpose), (3, 'rent'))
        kw = sf.to_tool_kwargs()
        self.assertEqual((kw['bedrooms'], kw['purpose']), (3, 'rent'))

    def test_search_tool_forwards_purpose_and_bedrooms(self):
        from apps.ai._tools_search import search_properties
        with patch('apps.properties.search.PropertySearchService.search', return_value=[]) as s:
            out = search_properties(city='Lahore', purpose='rent', bedrooms=2)
        self.assertEqual((s.call_args.kwargs['purpose'], s.call_args.kwargs['min_bedrooms']), ('rent', 2))
        self.assertFalse(out['live_search_pending'])
