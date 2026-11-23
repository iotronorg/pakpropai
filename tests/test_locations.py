"""
T-1060a — location hierarchy: seed, normalisation, matching (city, locality,
locality+sub-locality, unknown → unverified review queue), path maintenance,
merge, listing link via API/command/task, the location filter and the
locations endpoint.
"""
import importlib
import json
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.locations.matching import link_listing, link_unmatched, report
from apps.locations.models import Location
from apps.locations.services import match_location, merge, normalise
from apps.properties.models import Property
from tests.factories import make_developer, make_property, make_user

URL = '/api/v1/properties/'


def _loc(path):
    return Location.objects.get(path=path)


class SeedAndNormaliseTest(TestCase):
    def test_seed(self):
        self.assertEqual(_loc('pk').level, 'country')
        self.assertEqual(_loc('pk/punjab/lahore').level, 'city')
        self.assertIn('Defence', _loc('pk/punjab/lahore/dha').aliases)
        self.assertTrue(Location.objects.filter(path='ae/dubai/dubai/dubai-marina').exists())
        self.assertFalse(Location.objects.filter(is_verified=False).exists())

    def test_normalise(self):
        self.assertEqual(normalise('D.H.A Ph-05, Block #C'), 'dha phase 5 block c')
        self.assertEqual(normalise('P.E.C.H.S'), 'pechs')
        self.assertEqual(normalise('DHA  phase 5'), 'dha phase 5')
        self.assertEqual(normalise('  '), '')

    def test_seed_normaliser_is_frozen_copy(self):
        mig = importlib.import_module('apps.locations.migrations.0002_seed_locations')
        for text in ('D.H.A Ph-05', 'Gulshan-e-Iqbal Blk 13', 'F-7/2', 'Sec. 4 Rd No 12', 'لاہور'):
            self.assertEqual(mig._norm(text), normalise(text), text)


class MatchingTest(TestCase):
    def test_city_and_alias(self):
        self.assertEqual(match_location('PK', 'lahore', ''), _loc('pk/punjab/lahore'))
        self.assertEqual(match_location('PK', 'Pindi', ''), _loc('pk/punjab/rawalpindi'))

    def test_locality_by_name_and_alias(self):
        self.assertEqual(match_location('PK', 'Lahore', 'Gulberg'), _loc('pk/punjab/lahore/gulberg'))
        self.assertEqual(match_location('PK', 'Karachi', 'defence'), _loc('pk/sindh/karachi/dha'))
        self.assertEqual(match_location('PK', 'Lahore', 'Lahore'), _loc('pk/punjab/lahore'))

    def test_sub_locality_created_then_reused(self):
        a = match_location('PK', 'Lahore', 'DHA Phase 5')
        self.assertEqual((a.level, a.parent, a.is_verified), ('sub_locality', _loc('pk/punjab/lahore/dha'), False))
        self.assertEqual(match_location('PK', 'lahore', 'D.H.A. Ph-05'), a)          # same key
        self.assertEqual(match_location('PK', 'Lahore', 'Defence Phase 5'), a)       # via alias prefix

    def test_unknown_locality_and_city_go_to_review_queue(self):
        loc = match_location('PK', 'Lahore', 'Valencia Town')
        self.assertEqual((loc.level, loc.parent.path, loc.is_verified, loc.source),
                         ('locality', 'pk/punjab/lahore', False, 'listing'))
        city = match_location('PK', 'Okara', '')
        self.assertEqual((city.level, city.parent.path, city.is_verified), ('city', 'pk', False))

    def test_no_create_and_no_market(self):
        self.assertEqual(match_location('PK', 'Lahore', 'Nowhere', create=False), _loc('pk/punjab/lahore'))
        self.assertIsNone(match_location('PK', 'Okara', '', create=False))
        self.assertIsNone(match_location(None, 'Lahore', ''))
        self.assertIsNone(match_location('PK', '', 'DHA'))
        self.assertFalse(Location.objects.filter(is_verified=False).exists())


class TreeTest(TestCase):
    def test_clean_rules(self):
        lahore = _loc('pk/punjab/lahore')
        bad = Location(market_id='PK', parent=lahore, level='region', name='X')
        with self.assertRaises(ValidationError):
            bad.full_clean()
        cross = Location(market_id='AE', parent=lahore, level='locality', name='Y')
        with self.assertRaises(ValidationError):
            cross.full_clean()

    def test_rename_rewrites_descendant_paths(self):
        sub = match_location('PK', 'Lahore', 'Bahria Town Sector C')
        bahria = _loc('pk/punjab/lahore/bahria-town')
        self.assertEqual(sub.parent, bahria)
        bahria.slug = 'bahria-town-lahore'
        bahria.save()
        sub.refresh_from_db()
        self.assertTrue(sub.path.startswith('pk/punjab/lahore/bahria-town-lahore/'))

    def test_merge(self):
        _, org = make_developer()
        wrong = match_location('PK', 'Lahore', 'Gulbarg')                     # typo → review queue
        p = make_property(org=org, city='Lahore', location='Gulbarg')
        Property.objects.filter(pk=p.pk).update(location_ref=wrong)
        target = _loc('pk/punjab/lahore/gulberg')
        self.assertEqual(merge(wrong, target), 1)
        wrong.refresh_from_db()
        target.refresh_from_db()
        self.assertFalse(wrong.is_active)
        self.assertIn('Gulbarg', target.aliases)
        self.assertEqual(Property.objects.get(pk=p.pk).location_ref, target)
        self.assertEqual(match_location('PK', 'Lahore', 'gulbarg'), target)        # alias now matches
        with self.assertRaises(ValueError):
            merge(_loc('pk/punjab/lahore'), _loc('pk/punjab/lahore/dha'))       # into own descendant


class ListingLinkTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)

    def test_api_create_links_and_update_rematches(self):
        r = self.c.post(URL, {'title': 'H', 'city': 'Lahore', 'location': 'Gulberg', 'price': 1},
                        format='json')
        self.assertEqual(r.status_code, 201, r.data)
        p = Property.objects.get(title='H')
        self.assertEqual(p.location_ref, _loc('pk/punjab/lahore/gulberg'))
        p.owner = self.dev
        p.save(update_fields=['owner'])
        r = self.c.get(f'{URL}{p.pk}/')
        self.assertEqual(r.data['location_ref']['path'], 'pk/punjab/lahore/gulberg')
        r = self.c.patch(f'{URL}{p.pk}/', {'location': 'Model Town'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Property.objects.get(pk=p.pk).location_ref, _loc('pk/punjab/lahore/model-town'))
        r = self.c.patch(f'{URL}{p.pk}/', {'title': 'T2'}, format='json')          # text untouched
        self.assertEqual(Property.objects.get(pk=p.pk).location_ref, _loc('pk/punjab/lahore/model-town'))

    def test_explicit_location_id_and_market_check(self):
        dha = _loc('pk/punjab/lahore/dha')
        r = self.c.post(URL, {'title': 'H', 'city': 'Lahore', 'location': 'somewhere',
                              'price': 1, 'location_id': dha.pk}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Property.objects.get(title='H').location_ref, dha)
        self.assertFalse(Location.objects.filter(is_verified=False).exists())       # no matching ran
        r = self.c.post(URL, {'title': 'X', 'city': 'Dubai', 'location': 'Marina', 'price': 1,
                              'country': 'AE', 'currency': 'AED', 'location_id': dha.pk}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('location_id', r.data)

    def test_location_filter_includes_descendants(self):
        a = make_property(org=self.org, title='a')
        b = make_property(org=self.org, title='b')
        c = make_property(org=self.org, title='c')
        Property.objects.filter(pk=a.pk).update(location_ref=match_location('PK', 'Lahore', 'DHA Phase 6'))
        Property.objects.filter(pk=b.pk).update(location_ref=_loc('pk/punjab/lahore/dha'))
        Property.objects.filter(pk=c.pk).update(location_ref=_loc('pk/sindh/karachi/dha'))

        def titles(q):
            r = self.c.get(URL + q)
            self.assertEqual(r.status_code, 200, r.data)
            return sorted(x['title'] for x in (r.data['results'] if isinstance(r.data, dict) else r.data))
        self.assertEqual(titles(f"?location_id={_loc('pk/punjab/lahore/dha').pk}"), ['a', 'b'])
        self.assertEqual(titles(f"?location_id={_loc('pk/punjab').pk}"), ['a', 'b'])
        self.assertEqual(titles(f"?location_id={_loc('pk').pk}"), ['a', 'b', 'c'])
        self.assertEqual(self.c.get(URL + '?location_id=99999999').status_code, 400)

    def test_command_task_and_report(self):
        p1 = make_property(org=self.org, city='Karachi', location='Clifton')
        make_property(org=self.org, city='Lahore', location='Brand New Scheme')
        stats = link_unmatched()
        self.assertEqual(stats, {'checked': 2, 'linked': 2})
        self.assertEqual(Property.objects.get(pk=p1.pk).location_ref, _loc('pk/sindh/karachi/clifton'))
        rep = report()
        self.assertEqual((rep['unlinked'], rep['on_unverified_location'], rep['review_queue']), (0, 1, 1))
        out = StringIO()
        call_command('match_listing_locations', stdout=out)
        self.assertIn('checked 0, linked 0', out.getvalue())
        self.assertEqual(json.loads(out.getvalue().split('\n', 1)[1])['unlinked_pct'], 0.0)
        from apps.locations.tasks import link_unmatched_listings
        self.assertEqual(link_unmatched_listings(), {'checked': 0, 'linked': 0})

    def test_market_without_tree_clears_stale_link(self):
        p = make_property(org=self.org, city='Lahore', location='Gulberg')
        link_listing(p)
        self.assertIsNotNone(p.location_ref)
        Property.objects.filter(pk=p.pk).update(market_id=None)
        p = Property.objects.get(pk=p.pk)
        link_listing(p, force=True)
        self.assertIsNone(Property.objects.get(pk=p.pk).location_ref)


class LocationsEndpointTest(TestCase):
    def test_browse_and_autocomplete(self):
        c = APIClient()
        self.assertEqual(c.get('/api/v1/locations/').status_code, 401)
        c.force_authenticate(make_user(role='client'))
        lahore = _loc('pk/punjab/lahore')
        r = c.get(f'/api/v1/locations/?parent={lahore.pk}')
        names = {x['name'] for x in (r.data['results'] if isinstance(r.data, dict) else r.data)}
        self.assertIn('DHA', names)
        match_location('PK', 'Lahore', 'Unverified Place')
        r = c.get('/api/v1/locations/?market=pk&q=unverified')
        rows = r.data['results'] if isinstance(r.data, dict) else r.data
        self.assertEqual(rows, [])                                             # hidden from non-staff
        r = c.get('/api/v1/locations/?market=PK&level=city&q=lah')
        rows = r.data['results'] if isinstance(r.data, dict) else r.data
        self.assertEqual([x['path'] for x in rows], ['pk/punjab/lahore'])
        self.assertEqual(c.get('/api/v1/locations/?q=a').status_code, 400)
        self.assertEqual(c.get('/api/v1/locations/?market=PAK').status_code, 400)
        staff = make_user(role='admin', is_staff=True)
        c.force_authenticate(staff)
        r = c.get('/api/v1/locations/?q=unverified&include_unverified=1')
        rows = r.data['results'] if isinstance(r.data, dict) else r.data
        self.assertEqual(len(rows), 1)
