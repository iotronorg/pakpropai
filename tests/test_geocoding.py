"""
T-1060c — geocoding of location centroids (Google, HTTP mocked).

Client parsing and errors (the API key never reaches logs or exceptions),
enable flag, cache, daily budget, sanity checks (country, coarseness,
distance from the parent city, partial matches), due/refresh selection,
the command and task, and the public-point centroid fallback for listings
without their own coordinates.
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.geo import geocoding as g
from apps.locations.models import Location
from apps.locations.services import match_location, merge
from apps.properties.models import Property
from tests.factories import make_developer, make_property

KEY = 'AIza-test-secret-key'
ON = dict(GEOCODING_ENABLED=True, GOOGLE_MAPS_API_KEY=KEY, GEOCODING_DAILY_BUDGET=100)
S = Location.GeocodeStatus


def _loc(path):
    return Location.objects.get(path=path)


def _google(lat, lng, *, country='PK', types=('locality', 'political'), partial=False,
            place_id='pid', status='OK'):
    body = {'status': status, 'results': [] if status != 'OK' else [{
        'geometry': {'location': {'lat': lat, 'lng': lng}}, 'place_id': place_id,
        'formatted_address': 'somewhere', 'types': list(types),
        'address_components': [{'short_name': country, 'types': ['country', 'political']}],
        **({'partial_match': True} if partial else {})}]}
    resp = MagicMock(status_code=200)
    resp.json.return_value = body
    return resp


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.http = patch('requests.get').start()
        self.addCleanup(patch.stopall)


@override_settings(**ON)
class ClientTest(_Base):
    def test_enable_flag_and_key(self):
        self.assertIsInstance(g.get_geocoder(), g.GoogleGeocoder)
        with override_settings(GEOCODING_ENABLED=False):
            self.assertIsNone(g.get_geocoder())
        with override_settings(GOOGLE_MAPS_API_KEY=''):
            self.assertIsNone(g.get_geocoder())

    def test_parse_and_request_shape(self):
        self.http.return_value = _google(31.52, 74.35, place_id='abc')
        r = g.get_geocoder().geocode('Lahore, Punjab, Pakistan', country='PK')
        self.assertEqual((r.lat, r.lng, r.place_id, r.country, r.partial), (31.52, 74.35, 'abc', 'PK', False))
        params = self.http.call_args.kwargs['params']
        self.assertEqual((params['components'], params['region'], params['key']), ('country:PK', 'pk', KEY))
        self.assertTrue(self.http.call_args.kwargs['timeout'])

    def test_zero_and_errors_never_leak_the_key(self):
        self.http.return_value = _google(0, 0, status='ZERO_RESULTS')
        self.assertIsNone(g.get_geocoder().geocode('x', country='PK'))
        self.http.return_value = _google(0, 0, status='REQUEST_DENIED')
        with self.assertRaises(g.GeocodingError):
            g.get_geocoder().geocode('x', country='PK')
        self.http.side_effect = requests.ConnectionError(
            f'HTTPSConnectionPool: Max retries exceeded with url: /json?address=x&key={KEY}')
        with self.assertRaises(g.GeocodingError) as cm:
            g.get_geocoder().geocode('x', country='PK')
        self.assertNotIn(KEY, str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)
        with self.assertLogs('apps.geo.geocoding', level='WARNING') as logs:
            self.assertEqual(g.geocode_location(_loc('pk/punjab/lahore'), g.get_geocoder()), S.ERROR)
        self.assertNotIn(KEY, '\n'.join(logs.output))
        self.assertNotIn(KEY, _loc('pk/punjab/lahore').geocode_note)


@override_settings(**ON)
class GeocodeLocationTest(_Base):
    def test_city_then_listing_uses_centroid(self):
        _, org = make_developer()
        lahore = _loc('pk/punjab/lahore')
        no_coords = make_property(org=org, title='nc')
        own = make_property(org=org, title='own', latitude=Decimal('31.4'), longitude=Decimal('74.2'),
                            location_precision='exact')
        Property.objects.filter(pk__in=[no_coords.pk, own.pk]).update(location_ref=lahore)
        self.assertEqual(Property.objects.get(pk=no_coords.pk).geo_lat, None)
        self.http.return_value = _google(31.549722, 74.343611, place_id='ChIJ-lahore')
        self.assertEqual(g.geocode_location(lahore, g.get_geocoder()), S.OK)
        lahore.refresh_from_db()
        self.assertEqual((lahore.latitude, lahore.geocode_place_id), (Decimal('31.549722'), 'ChIJ-lahore'))
        self.assertEqual(self.http.call_args.kwargs['params']['address'], 'Lahore, Punjab, Pakistan')
        nc = Property.objects.get(pk=no_coords.pk)
        self.assertEqual((nc.geo_lat, nc.geo_lng), (Decimal('31.550000'), Decimal('74.340000')))
        self.assertEqual(Property.objects.get(pk=own.pk).geo_lat, Decimal('31.400000'))   # own point wins
        # In-memory mirror agrees with the trigger.
        self.assertEqual(nc._public_point(), (nc.geo_lat, nc.geo_lng))

    def test_sanity_checks(self):
        lahore = _loc('pk/punjab/lahore')
        Location.objects.filter(pk=lahore.pk).update(latitude=Decimal('31.55'), longitude=Decimal('74.34'))
        dha = _loc('pk/punjab/lahore/dha')
        cases = [
            (_google(24.86, 67.0), S.REJECTED, 'km from Lahore'),                 # Karachi's DHA
            (_google(31.47, 74.40, country='IN'), S.REJECTED, 'country IN'),
            (_google(31.5, 74.3, types=('administrative_area_level_1', 'political')), S.REJECTED, 'too coarse'),
            (_google(31.47, 74.40, partial=True), S.PARTIAL, ''),
            (_google(0, 0, status='ZERO_RESULTS'), S.ZERO, ''),
            (_google(31.47, 74.40, types=('sublocality', 'political')), S.OK, ''),
        ]
        for i, (resp, status, note) in enumerate(cases):
            with self.subTest(status=status, i=i):
                cache.clear()
                Location.objects.filter(pk=dha.pk).update(latitude=None, longitude=None)
                self.http.return_value = resp
                self.assertEqual(g.geocode_location(_loc(dha.path), g.get_geocoder()), status)
                d = _loc(dha.path)
                self.assertIn(note, d.geocode_note)
                self.assertEqual(d.latitude is not None, status == S.OK)      # only OK stores a centroid

    def test_cache_and_budget(self):
        self.http.return_value = _google(31.55, 74.34)
        geocoder = g.get_geocoder()
        g.cached_geocode(geocoder, 'Lahore, Punjab, Pakistan', country='PK')
        g.cached_geocode(geocoder, 'lahore, punjab, pakistan', country='PK')     # cached (case-insensitive)
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(g.budget_used(), 1)
        with override_settings(GEOCODING_DAILY_BUDGET=2):
            g.cached_geocode(geocoder, 'Karachi, Sindh, Pakistan', country='PK')
            with self.assertRaises(g.BudgetExhausted):
                g.cached_geocode(geocoder, 'Quetta, Balochistan, Pakistan', country='PK')
            stats = g.run(limit=10)
        self.assertTrue(stats['budget_exhausted'])
        self.assertEqual(self.http.call_count, 2)


@override_settings(**ON)
class DueAndRunTest(_Base):
    def test_due_selection(self):
        due = set(g.due_locations().values_list('path', flat=True))
        self.assertIn('pk/punjab/lahore', due)
        self.assertNotIn('pk/punjab', due)                         # regions are not geocoded
        self.assertFalse(any(p.startswith('ae/') for p in due))    # unlaunched market
        self.assertIn('ae/dubai/dubai', set(g.due_locations('AE').values_list('path', flat=True)))
        lahore = _loc('pk/punjab/lahore')
        Location.objects.filter(pk=lahore.pk).update(geocode_status=S.OK, geocoded_at=timezone.now())
        self.assertNotIn(lahore.path, set(g.due_locations().values_list('path', flat=True)))
        Location.objects.filter(pk=lahore.pk).update(geocoded_at=timezone.now() - timedelta(days=30))
        self.assertIn(lahore.path, set(g.due_locations().values_list('path', flat=True)))  # 30-day rule
        Location.objects.filter(pk=lahore.pk).update(geocode_status=S.MANUAL)
        self.assertNotIn(lahore.path, set(g.due_locations().values_list('path', flat=True)))
        Location.objects.filter(pk=lahore.pk).update(geocode_status=S.ZERO, geocoded_at=timezone.now())
        self.assertNotIn(lahore.path, set(g.due_locations().values_list('path', flat=True)))
        self.assertIn(lahore.path, set(g.due_locations(retry_failed=True).values_list('path', flat=True)))

    def test_run_command_and_task(self):
        self.http.return_value = _google(31.55, 74.34)
        out = StringIO()
        call_command('geocode_locations', '--limit', '3', stdout=out)
        self.assertIn('"budget_used_today": 3', out.getvalue())
        call_command('geocode_locations', '--dry-run', stdout=out)
        self.assertIn('→', out.getvalue())
        from apps.geo.tasks import geocode_locations
        self.assertIn('ok', geocode_locations(limit=1) | {'ok': 0})
        with override_settings(GEOCODING_ENABLED=False):
            self.assertEqual(g.run(), {'disabled': True, 'budget_exhausted': False})
            with self.assertRaises(CommandError):
                call_command('geocode_locations', stdout=StringIO())


class CentroidFallbackTest(TestCase):
    def test_link_and_merge_follow_the_centroid(self):
        _, org = make_developer()
        gulberg = _loc('pk/punjab/lahore/gulberg')
        Location.objects.filter(pk=gulberg.pk).update(latitude=Decimal('31.5204'), longitude=Decimal('74.3587'))
        from apps.locations.matching import link_listing
        p = make_property(org=org, city='Lahore', location='Gulberg')
        self.assertTrue(link_listing(p))
        self.assertEqual((p.geo_lat, p.geo_lng), (Decimal('31.520000'), Decimal('74.360000')))   # in memory
        self.assertEqual(Property.objects.get(pk=p.pk).geo_lat, Decimal('31.520000'))            # trigger
        typo = match_location('PK', 'Lahore', 'Gulbarg')
        q = make_property(org=org, city='Lahore', location='Gulbarg')
        Property.objects.filter(pk=q.pk).update(location_ref=typo)
        self.assertIsNone(Property.objects.get(pk=q.pk).geo_lat)
        merge(typo, gulberg)
        self.assertEqual(Property.objects.get(pk=q.pk).geo_lat, Decimal('31.520000'))

    def test_admin_coordinates_are_manual_and_refresh_listings(self):
        from django.contrib.admin.sites import site
        _, org = make_developer()
        loc = _loc('pk/punjab/lahore/model-town')
        p = make_property(org=org)
        Property.objects.filter(pk=p.pk).update(location_ref=loc)
        loc.latitude, loc.longitude = Decimal('31.4834'), Decimal('74.3260')
        form = MagicMock(changed_data=['latitude', 'longitude'])
        site._registry[Location].save_model(MagicMock(), loc, form, True)
        self.assertEqual(_loc(loc.path).geocode_status, S.MANUAL)
        self.assertEqual(Property.objects.get(pk=p.pk).geo_lat, Decimal('31.480000'))
