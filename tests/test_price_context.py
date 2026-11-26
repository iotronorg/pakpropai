"""T-4020 — indicative price context (FR-VAL-001…004, ADR-066)."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.locations.models import Location
from apps.properties import price_context as PC
from apps.properties.models import Property
from tests.factories import make_developer, make_org, make_property as _make
from tests.test_runtime import LOCMEM

URL = '/api/v1/price-context/'


def listing(price, size_m2=100, sandbox=False, **kw):
    kw.setdefault('country', 'PK')
    kw.setdefault('currency', 'PKR')
    kw.setdefault('city', 'Lahore')
    p = _make(title=kw.pop('title', 'L'), price=price, size_value=size_m2, size_unit='sqm', purpose='sale',
              price_type='total', category='residential', **kw)
    Property.objects.filter(pk=p.pk).update(is_sandbox=sandbox)
    p.is_sandbox = sandbox
    return p


def loc(name, level, parent=None):
    found = Location.objects.filter(market_id='PK', name=name, level=level, parent=parent).first()
    return found or Location.objects.create(market_id='PK', name=name, level=level, parent=parent)


@override_settings(CACHES=LOCMEM)
class ContextTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_hidden_below_threshold_then_distribution(self):
        for i in range(PC.MIN_SAMPLE - 1):
            listing(10_000_000 + i * 100_000)
        r = PC.context(country='PK', city='Lahore', currency='PKR', size_m2=100, category='residential')
        self.assertEqual((r['available'], r['reason'], r['n']), (False, 'too_few_comparables', PC.MIN_SAMPLE - 1))
        listing(10_700_000)
        r = PC.context(country='PK', city='lahore', currency='PKR', size_m2=100, category='residential',
                       asking_price=9_000_000)
        self.assertTrue(r['available'])
        self.assertEqual(r['comparables']['n'], 8)
        self.assertEqual(r['comparables']['scope'], 'city')
        self.assertEqual(r['price_per_m2']['median']['value'], 103_500)
        self.assertEqual({v['provenance'] for v in r['price_per_m2'].values()}, {'listing_asking_price'})
        self.assertEqual(r['subject']['position'], 'below_middle_half')
        self.assertEqual(r['subject']['percentile'], 0)
        self.assertEqual(r['confidence']['level'], 'low')               # tight spread, but only 8
        self.assertIn('few_comparables', r['confidence']['reasons'])
        self.assertIn('Not a valuation', r['disclaimer'])
        self.assertNotIn('estimated_value', r)

    def test_what_is_compared(self):
        for i in range(8):
            listing(10_000_000)
        listing(1, size_m2=300)                                      # outside the size band
        listing(1, currency='USD')                                   # other currency
        listing(1, city='Karachi')
        listing(1, sandbox=True)                                     # not public
        rent = listing(1)
        Property.objects.filter(pk=rent.pk).update(purpose='rent_long', price_type='per_month')
        por = listing(1)
        Property.objects.filter(pk=por.pk).update(price_on_request=True)
        r = PC.context(country='PK', city='Lahore', currency='PKR', size_m2=100)
        self.assertEqual((r['comparables']['n'], r['price_per_m2']['min']['value']), (8, 100_000))
        self.assertEqual(PC.context(country='PK', city='Lahore', currency='PKR', size_m2=0)['reason'], 'no_size')

    def test_locality_first_then_city(self):
        country = Location.objects.filter(market_id='PK', level='country').first() or loc('Pakistan', 'country')
        city = loc('Lahore', 'city', country)
        dha = loc('DHA', 'locality', city)
        phase = loc('Phase 5', 'sub_locality', dha)
        for _ in range(8):
            listing(20_000_000, location_ref=phase)
        for _ in range(8):
            listing(5_000_000)
        subject = listing(18_000_000, location_ref=phase)
        r = PC.for_listing(subject)
        self.assertEqual((r['comparables']['scope'], r['comparables']['place'], r['comparables']['n']),
                         ('locality', 'DHA', 8))                     # the subject itself is excluded
        self.assertEqual(r['price_per_m2']['median']['value'], 200_000)
        self.assertEqual(r['subject']['asking_price_per_m2']['value'], 180_000)
        other = listing(18_000_000, location_ref=loc('Gulberg', 'locality', city))
        self.assertEqual(PC.for_listing(other)['comparables']['scope'], 'city')

    def test_audit_carries_comparables_apart_from_benchmarks(self):
        from apps.audit.pdf import _comparables_block
        from apps.audit.services import AuditEngine
        for _ in range(8):
            listing(10_000_000)
        a = AuditEngine.run(city='Lahore', location='DHA', property_type='residential', estimated_value=12_000_000,
                            area_sqm=100)
        c = a['listing_comparables']
        self.assertTrue(c['available'])
        self.assertEqual(c['subject']['position'], 'above_middle_half')
        self.assertIn('benchmark_source', a['provenance'])                           # benchmarks unchanged
        self.assertTrue(_comparables_block(a, {'disclaimer': __import__('reportlab.lib.styles', fromlist=['x'])
                                               .getSampleStyleSheet()['Normal']}))
        self.assertEqual(AuditEngine.run(city='Dubai', location='Marina', property_type='residential',
                                         estimated_value=1, value_currency='AED',
                                         area_sqm=100)['listing_comparables']['reason'], 'market_not_supported')


@override_settings(CACHES=LOCMEM)
class ApiTest(TestCase):
    def setUp(self):
        cache.clear()
        for _ in range(8):
            listing(10_000_000)

    def test_criteria_and_validation(self):
        api = APIClient()
        r = api.get(URL, {'country': 'PK', 'city': 'Lahore', 'currency': 'PKR', 'size_m2': '100',
                          'category': 'residential'})
        self.assertEqual((r.status_code, r.data['available']), (200, True))
        for bad in ({}, {'country': 'PK', 'city': 'Lahore', 'currency': 'PKR'},
                    {'country': 'PK', 'city': 'Lahore', 'currency': 'PKR', 'size_m2': 'x'},
                    {'country': 'PK', 'city': 'Lahore', 'currency': 'PKR', 'size_m2': '-1'},
                    {'country': 'PK', 'city': 'Lahore', 'currency': 'PKR', 'size_m2': '1', 'category': 'castle'}):
            self.assertEqual(api.get(URL, bad).status_code, 400, bad)

    def test_listing_public_or_managed(self):
        public = listing(9_000_000)
        r = APIClient().get(URL, {'listing': str(public.pk)})
        self.assertEqual((r.status_code, r.data['listing_id'], r.data['subject']['percentile']),
                         (200, str(public.pk), 0))
        owner, org = make_developer(org=make_org(name='Prime'))
        draft = listing(9_000_000, sandbox=True, org=org, owner=owner)
        self.assertEqual(APIClient().get(URL, {'listing': str(draft.pk)}).status_code, 404)
        api = APIClient()
        api.force_authenticate(owner)
        self.assertEqual(api.get(URL, {'listing': str(draft.pk)}).status_code, 200)
