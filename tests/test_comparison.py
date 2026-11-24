"""T-2041 — comparison of 2–4 listings and approximate FX (ADR-039)."""
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai import grounding as G
from apps.ai import tool_registry as R
from apps.ai import tools
from apps.ai._tools_context import set_context
from apps.conversations import store
from apps.conversations.models import ConversationState
from apps.markets import fx
from apps.markets.models import FxRate
from apps.preferences import services as P
from apps.properties import comparison as K
from apps.properties import criteria as C
from apps.properties.models import Amenity, Property
from tests.factories import make_client, make_developer, make_org, make_property
from tests.test_runtime import LOCMEM

PUBLIC, MINE = '/api/v1/public/listings/compare/', '/api/v1/compare/'


@override_settings(CACHES=LOCMEM)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.org = make_org(operational_mode='production')
        self.user = make_client()

    def make(self, title='House', amenities=(), **kw):
        extra = {k: kw.pop(k) for k in ('is_sandbox', 'is_active', 'published_at', 'expires_at', 'legal_status',
                                        'currency') if k in kw}
        p = make_property(title=title, org=kw.pop('org', self.org), **{
            'bedrooms': 3, 'price': 10_000_000, 'size_value': 100, 'size_unit': 'sqm', **kw})
        Property.objects.filter(pk=p.pk).update(**{'is_sandbox': False, **extra})
        p.amenities.set(Amenity.objects.filter(key__in=amenities))
        p.refresh_from_db()
        return p

    def get(self, *ps, url=PUBLIC, api=None, **q):
        return (api or APIClient()).get(url, {'ids': ','.join(str(p.pk) for p in ps), **q})

    def rate(self, code, value, days=0):
        return FxRate.objects.create(currency=code, per_usd=value, source='Test Bank',
                                     as_of=timezone.now().date() - timedelta(days=days))


class FxTest(Base):
    def test_cross_rate_and_basis(self):
        self.rate('PKR', 280)
        self.rate('AED', '3.67', days=2)
        r = fx.rates({'PKR', 'AED', 'USD'})
        self.assertEqual((r.convert(2800, 'PKR', 'USD'), r.convert(10, 'USD', 'PKR'), r.convert(280_000, 'PKR', 'AED')),
                         (10, 2800, 3670))
        self.assertEqual(r.describe({'PKR', 'AED'})['as_of'], timezone.now().date() - timedelta(days=2))

    def test_old_future_and_missing_rates_are_not_used(self):
        self.rate('PKR', 280, days=8)
        self.rate('AED', 4, days=-1)
        r = fx.rates({'PKR', 'AED', 'GBP'})
        self.assertEqual([r.convert(1000, c, 'USD') for c in ('PKR', 'AED', 'GBP')], [None] * 3)

    def test_latest_rate_wins(self):
        self.rate('PKR', 100, days=3)
        self.rate('PKR', 280, days=1)
        self.assertEqual(fx.rates({'PKR'}).per_usd['PKR'], Decimal(280))

    def test_rates_are_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.rate('PKR', 0)

    def load(self, text, **kw):
        with tempfile.NamedTemporaryFile('w', suffix='.csv') as f:
            f.write(text)
            f.flush()
            call_command('import_fx_rates', f.name, verbosity=0, stdout=open('/dev/null', 'w'),
                         **{'as_of': timezone.now().date().isoformat(), 'source': 'Bank', **kw})

    def test_import(self):
        self.load('currency,per_usd\nPKR,280\naed,3.67\n')
        self.load('currency,per_usd\nPKR,281\n')
        self.assertEqual(dict(FxRate.objects.values_list('currency', 'per_usd')),
                         {'PKR': Decimal(281), 'AED': Decimal('3.67')})
        for bad in ('currency,per_usd\nGBP,0.8\nPKR,-1\n', 'currency,per_usd\nGBP,x\n', 'currency,per_usd\nGBP,NaN\n',
                    'currency,per_usd\nGBPX,1\n', 'a,b\n1,2\n'):
            with self.assertRaises(CommandError, msg=bad):
                self.load(bad)
        self.assertFalse(FxRate.objects.filter(currency='GBP').exists())      # all or none
        with self.assertRaises(CommandError):
            self.load('currency,per_usd\nGBP,1\n', as_of='2999-01-01')


class TableTest(Base):
    def test_facts_and_best(self):
        a = self.make('A', ['gym', 'lift'], price=10_000_000, size_value=100)
        b = self.make('B', ['lift'], price=12_000_000, size_value=200, bedrooms=None)
        out = K.compare(K.load([b.pk, a.pk]))
        self.assertEqual([i['id'] for i in out['items']], [str(b.pk), str(a.pk)])
        ib, ia = out['items']
        self.assertEqual((ia['price']['amount'], ia['price_per_m2']['amount'], ib['price_per_m2']['amount']),
                         (10_000_000, 100_000, 60_000))
        self.assertEqual((ib['bedrooms'], ia['size_m2'], out['fx']), (None, 100.0, None))
        self.assertEqual((out['shared_amenities'], ia['amenities_only_here'], ib['amenities_only_here']),
                         (['lift'], ['gym'], []))
        self.assertEqual((out['best']['lowest_price'], out['best']['lowest_price_per_m2'], out['best']['largest']),
                         (str(a.pk), str(b.pk), str(b.pk)))

    def test_rent_is_compared_per_month(self):
        a = self.make('A', purpose='rent_long', price_type='per_year', price=1_200_000)
        b = self.make('B', purpose='rent_long', price_type='per_month', price=110_000)
        out = K.compare(K.load([a.pk, b.pk]))
        self.assertEqual(out['items'][0]['price'], {'amount': 100_000, 'currency': 'PKR', 'basis': 'month',
                                                    'converted': None})
        self.assertEqual(out['best']['lowest_price'], str(a.pk))

    def test_not_comparable_has_no_best(self):
        sale, rent = self.make('S'), self.make('R', purpose='rent_long', price_type='per_month', price=90_000)
        self.assertIsNone(K.compare(K.load([sale.pk, rent.pk]))['best']['lowest_price'])
        tie = self.make('T')
        self.assertIsNone(K.compare(K.load([sale.pk, tie.pk]))['best']['lowest_price'])
        unsized = self.make('U', price=1_000_000, size_value=None)
        best = K.compare(K.load([sale.pk, unsized.pk]))['best']
        self.assertEqual((best['largest'], best['lowest_price_per_m2']), (None, None))
        foreign = self.make('F', price=5, currency='AED')
        self.assertIsNone(K.compare(K.load([sale.pk, foreign.pk]))['best']['lowest_price'])      # no rate

    def test_per_m2_price(self):
        a = self.make('A', price_type='per_sqm', price=50_000)
        item = K.compare(K.load([a.pk, self.make('B').pk]))['items'][0]
        self.assertEqual((item['price'], item['price_per_m2']['amount']), (None, 50_000))

    def test_hidden_price_never_shows(self):
        hidden = self.make('H', price=7_654_321, price_on_request=True, deposit=1_111_111)
        other = self.make('O')
        crit, _ = C.prepare(C.parse({'market': 'PK', 'price': {'max': 8_000_000}}))
        out = K.compare(K.load([hidden.pk, other.pk]), criteria=crit)
        h = out['items'][0]
        self.assertEqual((h['price'], h['price_per_m2'], h['deposit'], h['match']['price'], out['best']['lowest_price']),
                         (None, None, None, 'unknown', None))
        body = self.get(hidden, other, max_price='8000000').content.decode()
        for secret in ('7654321', '76543', '1111111'):
            self.assertNotIn(secret, body)

    def test_conversion_is_marked(self):
        self.rate('PKR', 280)
        self.rate('AED', '3.5')
        a, b = self.make('A', price=28_000_000), self.make('B', price=300_000, currency='AED')
        out = K.compare(K.load([a.pk, b.pk]), currency='USD')
        self.assertEqual([i['price']['converted'] for i in out['items']], [100_000, 85_714])
        self.assertEqual((out['fx']['approximate'], out['fx']['source'], out['best']['lowest_price']),
                         (True, 'Test Bank', str(b.pk)))


class MatchTest(Base):
    def match(self, p, **crit):
        c, _ = C.prepare(C.parse({'market': 'PK', **crit}))
        return K.match([p], c)[p.pk]

    def test_yes_no_unknown(self):
        p = self.make('A', ['lift'], price=10_000_000, bedrooms=3, bathrooms=None)
        self.assertEqual(
            self.match(p, purpose='sale', price={'max': 9_000_000}, bedrooms={'min': 3}, bathrooms={'min': 2},
                       amenities_required=['lift'], amenities_preferred=['gym'], location={'free_text': 'Lahore'},
                       size={'min': 200, 'unit': 'sqm'}, verified_only=True, category='residential'),
            {'purpose': 'yes', 'type': 'yes', 'price': 'no', 'bedrooms': 'yes', 'bathrooms': 'unknown',
             'amenity:lift': 'yes', 'amenity:gym': 'no', 'location': 'yes', 'size': 'no', 'verified': 'no'})

    def test_other_currency_is_unknown_and_other_market_is_not_in_the_place(self):
        p = self.make('A', price=10, currency='AED')
        self.assertEqual(self.match(p, price={'max': 9_000_000})['price'], 'unknown')
        Property.objects.filter(pk=p.pk).update(market='AE', country='AE')
        p.refresh_from_db()
        got = self.match(p, location={'free_text': 'Lahore'}, purpose='sale')
        self.assertEqual((got['location'], got['purpose']), ('no', 'yes'))

    def test_nothing_asked(self):
        self.assertEqual(self.match(self.make('A')), {})


class ApiTest(Base):
    def test_public(self):
        a, b = self.make('A'), self.make('B')
        r = self.get(a, b, min_bedrooms='3', market='PK')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['items'][0]['listing']['id'], str(a.pk))
        self.assertEqual(r.data['items'][0]['match'], {'bedrooms': 'yes'})
        self.assertIn('public', r['Cache-Control'])
        self.assertIsNone(self.get(a, b).data['items'][0]['match'])

    def test_validation(self):
        a, b = self.make('A'), self.make('B')
        for ids in ('', str(a.pk), f'{a.pk},{a.pk}', 'x,y', ','.join([f'{i:032x}' for i in range(5)])):
            self.assertEqual(APIClient().get(PUBLIC, {'ids': ids}).status_code, 400, ids)
        self.assertEqual(self.get(a, b, display_currency='dollars').status_code, 400)
        self.assertEqual(self.get(a, b, purpose='x').status_code, 400)

    def test_unavailable_listings(self):
        a, b = self.make('A'), self.make('B')
        for hidden in (self.make('C', is_sandbox=True), self.make('D', is_active=False),
                       self.make('E', expires_at=timezone.now() - timedelta(minutes=1))):
            r = self.get(a, hidden, b)
            self.assertEqual((len(r.data['items']), r.data['unavailable']), (2, [str(hidden.pk)]))
            self.assertEqual(self.get(a, hidden).status_code, 404)

    def test_private_contacts_are_removed(self):
        a = self.make('Call 03001234567', org=None, owner=self.user)
        self.assertNotIn('03001234567', self.get(a, self.make('B')).content.decode())

    def test_signed_in(self):
        a, b = self.make('A'), self.make('B')
        self.assertEqual(self.get(a, b, url=MINE).status_code, 401)
        api = APIClient()
        api.force_authenticate(self.user)
        r = self.get(a, b, url=MINE, api=api)
        self.assertEqual((r.status_code, r['Cache-Control']), (200, 'private, no-store'))

    def test_own_sandbox_listings_only_for_the_organization(self):
        dev, org = make_developer()
        box = self.make('Box', org=org, is_sandbox=True)
        b = self.make('B')
        mine, other = APIClient(), APIClient()
        mine.force_authenticate(dev)
        other.force_authenticate(make_developer()[0])
        self.assertEqual(self.get(box, b, url=MINE, api=mine).status_code, 200)
        self.assertEqual(self.get(box, b, url=MINE, api=other).status_code, 404)
        self.assertEqual(self.get(box, b, api=mine).status_code, 404)        # the public answer is the same for all

    def test_preferences_only_on_request_with_consent(self):
        a, b = self.make('A'), self.make('B', bedrooms=1)
        P.set_memory(self.user, True)
        P.state(self.user, 'PK', {'bedrooms': {'min': 3}})
        api = APIClient()
        api.force_authenticate(self.user)
        self.assertIsNone(self.get(a, b, url=MINE, api=api).data['items'][0]['match'])
        r = self.get(a, b, url=MINE, api=api, use_preferences='true')
        self.assertEqual([i['match'] for i in r.data['items']], [{'bedrooms': 'yes'}, {'bedrooms': 'no'}])
        r = self.get(a, b, url=MINE, api=api, use_preferences='true', min_bedrooms='1')
        self.assertEqual(r.data['items'][1]['match'], {'bedrooms': 'yes'})           # the parameter wins
        self.assertIsNone(self.get(a, b, api=api, use_preferences='true').data['items'][0]['match'])
        other = APIClient()
        other.force_authenticate(make_client())
        self.assertIsNone(self.get(a, b, url=MINE, api=other, use_preferences='true').data['items'][0]['match'])
        P.set_memory(self.user, False)
        self.assertIsNone(self.get(a, b, url=MINE, api=api, use_preferences='true').data['items'][0]['match'])


class ToolTest(Base):
    def setUp(self):
        super().setUp()
        self.phone = self.user.phone.lstrip('+')
        self.conv = store.open_whatsapp(self.phone, user=self.user)
        self.a, self.b = self.make('Canal View Home', price=10_000_000), self.make('Garden Town Home', price=12_000_000)
        self.state = ConversationState.objects.create(
            conversation=self.conv, last_results=[str(self.a.pk), str(self.b.pk)], active_criteria={})
        set_context(self.user, self.phone)

    def test_by_position_reference_and_search(self):
        ConversationState.objects.update(active_criteria={'market': 'PK', 'bedrooms': {'min': 3}})
        r = tools.compare_listings(f'1, {self.b.ref_no}')
        self.assertEqual((r['found'], r['detail'], [x['title'] for x in r['properties']]),
                         (True, True, ['Canal View Home', 'Garden Town Home']))
        self.assertEqual((r['best']['lowest_price'], r['match_against'], r['properties'][0]['match']),
                         (1, 'current search', {'bedrooms': 'yes'}))

    def test_saved_preferences_when_nothing_is_searched(self):
        P.set_memory(self.user, True)
        P.state(self.user, 'PK', {'bedrooms': {'min': 5}})
        r = tools.compare_listings('1,2')
        self.assertEqual((r['match_against'], r['properties'][0]['match']), ('saved preferences', {'bedrooms': 'no'}))
        set_context(None, self.phone)
        self.assertNotIn('match_against', tools.compare_listings('1,2'))

    def test_refusals(self):
        for refs in ('', '1', '1,1', '1,9', 'x,y'):
            self.assertFalse(tools.compare_listings(refs)['found'], refs)
        more = [self.make(f'M{i}') for i in range(3)]
        self.assertFalse(tools.compare_listings('1,2,' + ','.join(p.ref_no for p in more))['found'])
        self.assertEqual(tools.compare_listings('1,2,9')['not_found'], ['9'])

    def test_is_read_only_and_json(self):
        import json
        self.rate('PKR', 280)
        spec = R.REGISTRY['compare_listings']
        self.assertFalse(spec.commits or spec.requires_confirmation)
        r = tools.compare_listings('1,2', 'usd')
        json.dumps(r)
        self.assertEqual(r['fx']['as_of'], timezone.now().date().isoformat())

    def check(self, reply):
        e = G.Evidence('compare them', [], '')
        e.add(tools.compare_listings('1,2', 'USD'))
        return G.check(reply, e)

    def test_two_amounts_in_a_row_are_both_read(self):
        self.assertEqual(G.money('PKR 10,000,000, PKR 150,000.'),
                         [(Decimal(10_000_000), 'PKR'), (Decimal(150_000), 'PKR')])

    def test_grounding(self):
        self.rate('PKR', 280)
        self.assertTrue(self.check('Canal View Home: PKR 10,000,000, PKR 100,000 per m², about USD 35,714.').ok)
        self.assertFalse(self.check('Canal View Home: PKR 12,000,000.').ok)          # the other one's price
        self.assertFalse(self.check('Canal View Home: PKR 10,000,000, PKR 150,000 per m².').ok)
        self.assertFalse(self.check('Canal View Home: PKR 120,000 per m².').ok)
        self.assertFalse(self.check('Canal View Home: about AED 35,714.').ok)
