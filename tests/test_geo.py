"""
T-1060b — geo search on the listing public point.

Public point (trigger + save mirror; snapped for private/approximate
listings), backfill, radius/bbox search in plain SQL and with PostGIS
(parity), the listings API filters/ordering, coordinate masking by viewer,
validation and geo_setup. The WhatsApp pin resolver (tier 2) is covered in
tests/test_discovery_engine.py.
"""
import importlib
import math
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.geo import search as geo
from apps.properties.models import Property
from tests.factories import make_developer, make_property, make_user

URL = '/api/v1/properties/'
LAT, LNG = 31.500000, 74.300000            # Lahore


def _db_point(pk):
    return tuple(Property.objects.filter(pk=pk).values_list('geo_lat', 'geo_lng').get())


def _hav(lat1, lng1, lat2, lng2):
    r = math.radians
    a = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lng2 - lng1) / 2) ** 2
    return 2 * geo.EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _exact(org, title, lat, lng, **kw):
    return make_property(org=org, title=title, latitude=Decimal(str(lat)), longitude=Decimal(str(lng)),
                         location_precision='exact', **kw)


class PublicPointTest(TestCase):
    def setUp(self):
        _, self.org = make_developer()

    def test_rules(self):
        exact = _exact(self.org, 'e', '31.512345', '74.345678')
        approx = make_property(org=self.org, latitude=Decimal('31.512345'), longitude=Decimal('74.345678'))
        client = make_property(owner=make_user(role='client'), latitude=Decimal('31.512345'),
                               longitude=Decimal('74.345678'), location_precision='exact')
        none = make_property(org=self.org)
        self.assertEqual(_db_point(exact.pk), (Decimal('31.512345'), Decimal('74.345678')))
        self.assertEqual(_db_point(approx.pk), (Decimal('31.510000'), Decimal('74.350000')))
        self.assertEqual(_db_point(client.pk), (Decimal('31.510000'), Decimal('74.350000')))
        self.assertEqual(_db_point(none.pk), (None, None))
        for p in (exact, approx, client, none):                  # in-memory mirror == trigger
            self.assertEqual((p.geo_lat, p.geo_lng), _db_point(p.pk))

    def test_bulk_and_partial_updates_recompute(self):
        p = _exact(self.org, 'e', '31.512345', '74.345678')
        Property.objects.filter(pk=p.pk).update(location_precision='approximate')
        self.assertEqual(_db_point(p.pk), (Decimal('31.510000'), Decimal('74.350000')))
        p = Property.objects.get(pk=p.pk)
        p.latitude = Decimal('30.004999')
        p.save(update_fields=['latitude'])
        self.assertEqual(_db_point(p.pk)[0], Decimal('30.000000'))
        self.assertEqual(p.geo_lat, Decimal('30.000000'))
        Property.objects.filter(pk=p.pk).update(geo_lat=1, geo_lng=1)      # cannot be forged
        self.assertEqual(_db_point(p.pk), (Decimal('30.000000'), Decimal('74.350000')))

    def test_backfill(self):
        p = _exact(self.org, 'e', '31.5', '74.3')
        with connection.cursor() as cur:
            cur.execute("SET session_replication_role = replica")
            cur.execute("UPDATE properties SET geo_lat = NULL, geo_lng = NULL WHERE id = %s", [p.pk])
            cur.execute("SET session_replication_role = DEFAULT")
        importlib.import_module('apps.properties.migrations.0043_backfill_geo_point').forwards(
            None, connection.schema_editor())
        self.assertEqual(_db_point(p.pk), (Decimal('31.500000'), Decimal('74.300000')))


class RadiusSearchTest(TestCase):
    def setUp(self):
        _, self.org = make_developer()
        self.a = _exact(self.org, 'a', LAT, LNG)
        self.b = _exact(self.org, 'b', LAT + 0.05, LNG)          # ~5.6 km north
        self.c = _exact(self.org, 'c', LAT + 0.5, LNG)           # ~55.6 km north
        _exact(self.org, 'far', -33.9, 151.2)                    # Sydney
        make_property(org=self.org, title='nocoords')

    def _run(self, km):
        rows = geo.within_radius(Property.objects.all(), LAT, LNG, km).order_by('distance_km')
        return [(p.title, p.distance_km) for p in rows]

    def _check(self, rel_tol):
        r10 = self._run(10)
        self.assertEqual([t for t, _ in r10], ['a', 'b'])
        self.assertAlmostEqual(r10[0][1], 0.0, places=3)
        self.assertTrue(math.isclose(r10[1][1], _hav(LAT, LNG, LAT + 0.05, LNG), rel_tol=rel_tol))
        self.assertEqual([t for t, _ in self._run(60)], ['a', 'b', 'c'])
        self.assertEqual([t for t, _ in self._run(1)], ['a'])

    def test_plain_sql(self):
        self.assertFalse(geo.postgis_ready())
        self._check(rel_tol=1e-6)
        sql = str(geo.within_radius(Property.objects.all(), LAT, LNG, 5).query)
        self.assertIn('"geo_lat" >=', sql)                     # bbox prefilter (btree)
        self.assertNotIn('ST_DWithin', sql)

    @override_settings(GEO_POSTGIS_ENABLED=True)
    def test_postgis_parity(self):
        out = StringIO()
        with connection.cursor() as cur:
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")    # flush deferred FK checks first
        call_command('geo_setup', stdout=out)
        self.assertIn('PostGIS ready', out.getvalue())
        try:
            self.assertTrue(geo.postgis_ready())
            self._check(rel_tol=5e-3)                      # spheroid vs sphere: < 0.5 %
            sql = str(geo.within_radius(Property.objects.all(), LAT, LNG, 5).query)
            self.assertIn('ST_DWithin', sql)
            call_command('geo_setup', '--check', stdout=out)
            self.assertIn('installed=True gist_index=True', out.getvalue())
        finally:
            geo.reset_ready_cache()

    @override_settings(GEO_POSTGIS_ENABLED=True)
    def test_flag_on_without_extension_falls_back(self):
        geo.reset_ready_cache()
        with connection.cursor() as cur:
            cur.execute("DROP EXTENSION IF EXISTS postgis CASCADE")
        try:
            self.assertFalse(geo.postgis_ready())
            self._check(rel_tol=1e-6)
        finally:
            geo.reset_ready_cache()

    def test_bbox_and_validation(self):
        rows = geo.within_bbox(Property.objects.all(), LNG - 0.1, LAT - 0.1, LNG + 0.1, LAT + 0.1)
        self.assertEqual(sorted(p.title for p in rows), ['a', 'b'])
        with self.assertRaises(ValueError):
            geo.within_bbox(Property.objects.all(), 1, 1, 0, 0)
        with self.assertRaises(ValueError):
            geo.within_radius(Property.objects.all(), 91, 0, 5)
        self.assertEqual(geo.bbox_for_radius(90, 0, 10)[2:], (-180.0, 180.0))


class ApiGeoTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.a = _exact(self.org, 'a', '31.512345', '74.345678', owner=self.dev)
        self.b = make_property(org=self.org, title='b', owner=self.dev,
                               latitude=Decimal('31.552345'), longitude=Decimal('74.345678'))
        self.c = APIClient()

    def _rows(self, client, q):
        r = client.get(URL + q)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data['results'] if isinstance(r.data, dict) else r.data

    def test_near_ordering_and_masking(self):
        Property.objects.filter(pk__in=[self.a.pk, self.b.pk]).update(is_sandbox=False)
        outsider = make_user(role='client')                 # consumers see other orgs' listings
        self.c.force_authenticate(outsider)
        rows = self._rows(self.c, '?near=31.512345,74.345678&radius_km=10&ordering=distance_km')
        self.assertEqual([x['title'] for x in rows], ['a', 'b'])
        a, b = rows
        self.assertEqual((a['latitude'], a['distance_km']), ('31.512345', 0.0))        # exact listing
        self.assertEqual((b['latitude'], b['longitude']), ('31.550000', '74.350000'))  # snapped
        self.assertEqual(b['distance_km'], float(round(b['distance_km'])))             # whole km
        self.c.force_authenticate(self.dev)                     # owner / org member: exact
        rows = self._rows(self.c, '?near=31.512345,74.345678&radius_km=10&ordering=distance_km')
        self.assertEqual(rows[1]['latitude'], '31.552345')
        d = self.c.get(f'{URL}{self.b.pk}/').data
        self.assertEqual(d['latitude'], '31.552345')

    def test_bbox_and_no_distance_without_near(self):
        self.c.force_authenticate(self.dev)
        rows = self._rows(self.c, '?bbox=74.3,31.5,74.4,31.53')
        self.assertEqual([x['title'] for x in rows], ['a'])
        self.assertIsNone(rows[0]['distance_km'])

    def test_ordering_distance_for_user_without_org(self):
        self.c.force_authenticate(make_user(role='agent'))   # no org: empty, not a 500
        self.assertEqual(self._rows(self.c, '?near=31.5,74.3&ordering=distance_km'), [])

    def test_validation(self):
        self.c.force_authenticate(self.dev)
        for q in ('?near=31.5', '?near=a,b', '?near=95,74', '?near=31.5,74.3&radius_km=1000',
                  '?near=31.5,74.3&radius_km=-1', '?ordering=distance_km', '?bbox=1,2,3',
                  '?bbox=74.4,31.5,74.3,31.6'):
            with self.subTest(q=q):
                self.assertEqual(self.c.get(URL + q).status_code, 400)

    def test_write_coordinates(self):
        self.c.force_authenticate(self.dev)
        r = self.c.patch(f'{URL}{self.b.pk}/', {'latitude': '33.6844', 'longitude': '73.0479'},
                         format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(_db_point(self.b.pk), (Decimal('33.680000'), Decimal('73.050000')))
        self.assertEqual(r.data['latitude'], '33.684400')                       # owner sees exact
        bad = self.c.patch(f'{URL}{self.b.pk}/', {'latitude': '95', 'longitude': '73'}, format='json')
        self.assertEqual(bad.status_code, 400)
        r = self.c.post(URL, {'title': 'N', 'city': 'Lahore', 'location': 'DHA', 'price': 1,
                              'latitude': '31.5'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('latitude', r.data)
