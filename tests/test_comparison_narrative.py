"""T-4030 — comparison narrative and running costs (FR-CMP-003/004, ADR-067)."""
from unittest.mock import patch

from django.core.cache import cache

from apps.ai import comparison_narrative as N
from apps.config.models import SystemConfig
from apps.properties import comparison as K
from rest_framework.test import APIClient
from tests.factories import make_client
from tests.test_comparison import Base

URL = '/api/v1/compare/narrative/'


class RunningCostsTest(Base):
    def test_sale_listings_get_estimates_rent_and_hidden_do_not(self):
        a = self.make('A', country='PK', price=30_000_000, service_charge=120_000)
        b = self.make('B', country='PK', price=10_000_000)
        rent = self.make('R', country='PK', purpose='rent_long', price_type='per_month', price=100_000)
        hidden = self.make('H', country='PK', price_on_request=True)
        out = K.compare([a, b, rent, hidden])
        rc = {i['id']: i['running_costs'] for i in out['items']}
        ra = rc[str(a.pk)]
        self.assertTrue(ra['estimate'])
        self.assertEqual(ra['purchase_costs']['source'], 'market_calculator')
        self.assertEqual(ra['purchase_costs']['basis'], 'once')
        self.assertGreater(ra['annual_property_tax']['amount'], 0)                   # above the PK 7E threshold
        self.assertEqual(ra['annual_service_charge']['amount'], 120_000)
        self.assertEqual(ra['annual_total']['amount'], ra['annual_property_tax']['amount'] + 120_000)
        self.assertEqual(rc[str(b.pk)]['annual_property_tax']['amount'], 0)
        self.assertIsNone(rc[str(rent.pk)])
        self.assertIsNone(rc[str(hidden.pk)])
        zz = self.make('Z', country='ZZ', price=1_000)
        self.assertIsNone(K.compare([a, zz])['items'][1]['running_costs'])

    def test_api_carries_running_costs(self):
        a, b = self.make('A', country='PK'), self.make('B', country='PK', price=12_000_000)
        r = self.get(a, b)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('purchase_costs', r.data['items'][0]['running_costs'])


class CheckTest(Base):
    def setUp(self):
        super().setUp()
        self.a = self.make('A', country='PK', price=10_000_000, bedrooms=3)
        self.b = self.make('B', country='PK', price=12_500_000, bedrooms=None)
        self.out = K.compare([self.a, self.b])
        self.known = N.cells(self.out)

    def test_numbers_must_come_from_cited_cells(self):
        ok = 'Listing A asks PKR 10,000,000 and Listing B PKR 12,500,000, 2,500,000 more.'
        self.assertEqual(N.check(ok, ['A.price', 'B.price'], self.known), [])
        self.assertEqual(N.check('Listing B asks 1.25 crore.', ['B.price'], self.known), [])
        self.assertEqual(N.check('Listing A asks PKR 10,000,000.', ['B.price'], self.known), ['number_not_in_cells'])
        self.assertEqual(N.check('Listing A has 3 bedrooms.', ['A.bedrooms'], self.known), [])
        self.assertEqual(N.check('Listing A has 7 bedrooms.', ['A.bedrooms'], self.known), ['number_not_in_cells'])
        self.assertEqual(N.check('A nice home.', [], self.known), ['no_cell'])
        self.assertEqual(N.check('Listing A.', ['A.view'], self.known), ['unknown_cell'])
        self.assertEqual(N.check('Listing A is the best value.', ['A.price'], self.known), ['judgement_or_advice'])
        self.assertEqual(N.check('Listing B is near good schools.', ['B.location'], self.known),
                         ['judgement_or_advice'])

    def test_table_and_unknowns(self):
        t = N.table(self.out)
        self.assertIn('A.price: PKR 10,000,000', t)
        self.assertIn('B.bedrooms: not specified', t)
        self.assertIn({'row': 'bedrooms', 'label': 'bedrooms', 'not_specified_for': ['B']}, N.unknowns(self.out))

    def test_narrative_keeps_only_checked_sentences(self):
        reply = {'sentences': [{'text': 'Listing A asks PKR 10,000,000; Listing B asks PKR 12,500,000.',
                                'cells': ['A.price', 'B.price']},
                               {'text': 'Listing B is a great deal.', 'cells': ['B.price']},
                               {'text': 'Listing B has 5 bedrooms.', 'cells': ['B.bedrooms']},
                               'junk', {'text': '', 'cells': []}]}
        with patch('apps.ai.llm.complete_json', return_value=reply) as m:
            out = N.narrative(self.out, country='PK')
        self.assertEqual([s['text'] for s in out['sentences']], [reply['sentences'][0]['text']])
        self.assertEqual([x['problems'] for x in out['left_out']], [['judgement_or_advice'], ['number_not_in_cells']])
        self.assertEqual(out['listings'], {'A': str(self.a.pk), 'B': str(self.b.pk)})
        self.assertEqual(m.call_args.kwargs['interaction_type'], 'comparison_narrative')


class ApiTest(Base):
    def test_auth_flag_and_unavailable(self):
        from apps.ai.llm import LLMUnavailable
        a, b = self.make('A', country='PK'), self.make('B', country='PK', price=12_000_000)
        q = {'ids': f'{a.pk},{b.pk}'}
        self.assertIn(APIClient().get(URL, q).status_code, (401, 403))
        api = APIClient()
        api.force_authenticate(make_client())
        reply = {'sentences': [{'text': 'Listing B asks PKR 12,000,000.', 'cells': ['B.price']}]}
        with patch('apps.ai.llm.complete_json', return_value=reply):
            r = api.get(URL, q)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['narrative']['sentences'][0]['cells'], ['B.price'])
        self.assertEqual(len(r.data['items']), 2)
        with patch('apps.ai.llm.complete_json', side_effect=LLMUnavailable('x')):
            r = api.get(URL, q)
        self.assertEqual((r.status_code, r.data['code'], len(r.data['items'])), (503, 'ai.unavailable', 2))
        self.assertEqual(api.get(URL, {'ids': str(a.pk)}).status_code, 400)
        SystemConfig.objects.update_or_create(key='feature_comparison_narrative', defaults={'value': 'false'})
        cache.clear()
        self.assertEqual(api.get(URL, q).status_code, 403)
