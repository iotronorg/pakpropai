"""
T-1070b — size rename (size_value/size_unit/size_m2): readers on the
canonical size (KI-049) and the deprecated API aliases. The v1 mirror is a DB
trigger since T-1070g-1 (tests/test_listing_contract.py).
"""
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.properties.models import Property
from apps.properties.search import PropertySearchService
from tests.factories import make_developer, make_property

URL = '/api/v1/properties/'


def _raw_v1_write(pk, value, unit):
    """What a pre-v2 pod does: writes only the deprecated columns."""
    with connection.cursor() as cur:
        cur.execute("UPDATE properties SET area_marla=%s, area_unit=%s WHERE id=%s", [value, unit, pk])


class CanonicalSizeReadersTest(TestCase):
    """KI-049: readers treated area_marla as marla whatever area_unit said."""

    def setUp(self):
        _, self.org = make_developer()

    def test_size_helpers(self):
        p = make_property(org=self.org, area_marla=None, size_value=1, size_unit='kanal')
        self.assertEqual(p.size_in('marla'), 20.0)
        self.assertEqual(p.size_display(), '1 kanal')
        q = make_property(org=self.org, area_marla=None, size_value=1200, size_unit='sqft', location='X')
        self.assertEqual(q.size_display(), '1,200 sqft')

    def test_price_signal_uses_marla_equivalent(self):
        from apps.properties.scoring import PropertyScoringEngine
        kanal = make_property(org=self.org, area_marla=None, size_value=1, size_unit='kanal',
                              price=60_000_000, location='Model Town')
        marla = make_property(org=self.org, area_marla=20, price=60_000_000, location='Model Town')
        self.assertEqual(PropertyScoringEngine.compute_signals(kanal)['price_signal'],
                         PropertyScoringEngine.compute_signals(marla)['price_signal'])

    def test_search_size_band_in_real_size(self):
        make_property(org=self.org, title='One kanal', area_marla=None, size_value=1, size_unit='kanal',
                      location='A', price=1_000_000)
        make_property(org=self.org, title='Sqft five marla', area_marla=None, size_value=1361,
                      size_unit='sqft', location='B', price=2_000_000)
        make_property(org=self.org, title='Five marla', area_marla=5, location='C', price=3_000_000)

        def titles(m):
            return sorted(r.title for r in PropertySearchService._from_db(
                city='Lahore', area_marla=m, viewer_org=self.org))

        self.assertEqual(titles(20), ['One kanal'])
        self.assertEqual(titles(5), ['Five marla', 'Sqft five marla'])

    def test_search_result_shows_listed_unit(self):
        make_property(org=self.org, title='K', area_marla=None, size_value=1, size_unit='kanal')
        r = PropertySearchService._from_db(city='Lahore', viewer_org=self.org)[0]
        self.assertEqual((r.area_marla, r.size_value, r.size_unit), (20.0, 1.0, 'kanal'))
        self.assertIn('1 kanal', r.format_wa())


class ApiTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)

    def _create(self, **extra):
        return self.c.post(URL, {'title': 'S', 'city': 'Lahore', 'location': 'DHA', 'price': 1, **extra},
                           format='json')

    def test_create_with_v2_size(self):
        r = self._create(size_value='10', size_unit='marla')
        self.assertEqual(r.status_code, 201, r.data)
        d = self.c.get(f"{URL}{Property.objects.get(title='S').pk}/").data
        self.assertEqual((d['size_value'], d['size_unit'], d['area_marla']), ('10.00', 'marla', '10.00'))
        self.assertIsNotNone(d['size_m2'])

    def test_conflicting_alias_rejected(self):
        r = self._create(size_value='10', area_marla='5')
        self.assertEqual(r.status_code, 400)
        self.assertIn('area_marla', r.data)

    def test_deprecated_alias_still_writes(self):
        p = make_property(org=self.org, owner=self.dev, area_marla=5)
        r = self.c.patch(f'{URL}{p.pk}/', {'area_marla': '6'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['size_value'], '6.00')
        r = self.c.patch(f'{URL}{p.pk}/', {'size_value': '7', 'size_unit': 'kanal'}, format='json')
        self.assertEqual((r.data['area_marla'], r.data['area_unit']), ('7.00', 'kanal'))

    def test_size_m2_is_read_only(self):
        p = make_property(org=self.org, owner=self.dev, area_marla=5)
        self.c.patch(f'{URL}{p.pk}/', {'size_m2': '1'}, format='json')
        p.refresh_from_db()
        self.assertNotEqual(p.size_m2, Decimal('1'))

    def test_openapi_marks_alias_deprecated(self):
        from drf_spectacular.generators import SchemaGenerator
        schema = SchemaGenerator().get_schema(request=None, public=True)
        props = schema['components']['schemas']['PropertyDetail']['properties']
        self.assertIn('Deprecated', props['area_marla'].get('description', ''))
        self.assertIn('size_value', props)
