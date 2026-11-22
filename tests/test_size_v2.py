"""
T-1070b — size rename (size_value/size_unit/size_m2) with dual-write,
backfill/drift repair, and readers on the canonical size (KI-049).
"""
import importlib
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.properties.models import Property
from apps.properties.search import PropertySearchService
from apps.properties.size_sync import count_drift
from tests.factories import make_developer, make_property

URL = '/api/v1/properties/'


def _raw_v1_write(pk, value, unit):
    """What a pre-v2 pod does: writes only the deprecated columns."""
    with connection.cursor() as cur:
        cur.execute("UPDATE properties SET area_marla=%s, area_unit=%s WHERE id=%s", [value, unit, pk])


class DualWriteTest(TestCase):
    def setUp(self):
        _, self.org = make_developer()

    def test_new_with_v1_only_mirrors_to_v2(self):
        p = make_property(org=self.org, area_marla=5)
        p.refresh_from_db()
        self.assertEqual((p.size_value, p.size_unit), (Decimal('5.00'), 'marla'))
        self.assertEqual(p.size_m2, p.area_sqm)
        self.assertAlmostEqual(float(p.size_m2), 126.4645, places=3)

    def test_new_with_v2_mirrors_to_v1(self):
        p = make_property(org=self.org, area_marla=None, size_value=1, size_unit='kanal')
        p.refresh_from_db()
        self.assertEqual((p.area_marla, p.area_unit), (Decimal('1.00'), 'kanal'))
        self.assertAlmostEqual(float(p.size_m2), 505.857, places=2)

    def test_loaded_object_whichever_pair_changed_wins(self):
        p = Property.objects.get(pk=make_property(org=self.org, area_marla=5).pk)
        p.size_value = 8
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.area_marla, Decimal('8.00'))
        p = Property.objects.get(pk=p.pk)
        p.area_marla, p.area_unit = 2, 'kanal'      # a v1 writer
        p.save()
        p.refresh_from_db()
        self.assertEqual((p.size_value, p.size_unit), (Decimal('2.00'), 'kanal'))

    def test_update_fields_carry_the_mirror(self):
        p = Property.objects.get(pk=make_property(org=self.org, area_marla=5).pk)
        p.size_value = 7
        p.save(update_fields=['size_value'])
        p.refresh_from_db()
        self.assertEqual((p.area_marla, p.size_m2 is not None), (Decimal('7.00'), True))
        self.assertAlmostEqual(float(p.size_m2), 7 * 25.2929, places=3)

    def test_deferred_load_does_not_clobber(self):
        pk = make_property(org=self.org, area_marla=5).pk
        _raw_v1_write(pk, 9, 'marla')                # drift from an old pod
        p = Property.objects.only('id', 'title').get(pk=pk)
        p.title = 'Renamed'
        p.save()                                     # v1 (newer) must win, not stale v2
        p.refresh_from_db()
        self.assertEqual((p.area_marla, p.size_value), (Decimal('9.00'), Decimal('9.00')))

    def test_unsaved_size_survives_unrelated_partial_save(self):
        p = Property.objects.get(pk=make_property(org=self.org, area_marla=5).pk)
        p.size_value = 9                             # in memory only
        p.title = 'T3'
        p.save(update_fields=['title'])
        p.save(update_fields=['size_value'])
        p.refresh_from_db()
        self.assertEqual((p.size_value, p.area_marla), (Decimal('9.00'), Decimal('9.00')))

    def test_unrelated_update_fields_untouched(self):
        p = Property.objects.get(pk=make_property(org=self.org, area_marla=5).pk)
        p.title = 'T2'
        p.save(update_fields=['title'])
        p.refresh_from_db()
        self.assertEqual(p.title, 'T2')


class BackfillAndDriftTest(TestCase):
    def test_migration_backfill_and_command(self):
        _, org = make_developer()
        pk = make_property(org=org, area_marla=5).pk
        with connection.cursor() as cur:
            cur.execute("UPDATE properties SET size_value=NULL, size_unit=NULL, size_m2=NULL WHERE id=%s", [pk])
        self.assertEqual(count_drift(), 1)
        mig = importlib.import_module('apps.properties.migrations.0028_backfill_size_v2')
        with connection.schema_editor() as se:
            mig.backfill(None, se)
        self.assertEqual(count_drift(), 0)
        self.assertEqual(Property.objects.get(pk=pk).size_value, Decimal('5.00'))

        _raw_v1_write(pk, 3, 'kanal')
        with connection.cursor() as cur:
            cur.execute("UPDATE properties SET area_sqm=%s WHERE id=%s", [3 * 505.857, pk])
        out = StringIO()
        call_command('sync_listing_size', '--dry-run', stdout=out)
        self.assertIn('1 listing', out.getvalue())
        call_command('sync_listing_size', stdout=StringIO())
        p = Property.objects.get(pk=pk)
        self.assertEqual((p.size_value, p.size_unit), (Decimal('3.00'), 'kanal'))
        self.assertEqual(count_drift(), 0)


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
