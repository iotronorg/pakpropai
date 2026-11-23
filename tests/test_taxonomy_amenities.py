"""
T-1070d — listing category/subtype taxonomy (property_type is a derived,
deprecated alias since T-1070g-1) and the amenity taxonomy: model rules,
seed, API read/write by key, list filters, the taxonomy endpoint, and the
search service / AI tool amenity filter.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.properties.models import Amenity, ListingAmenity, Property
from apps.properties import taxonomy
from tests.factories import make_developer, make_property, make_user

URL = '/api/v1/properties/'


def _amen(p, *keys):
    for k in keys:
        ListingAmenity.objects.create(property=p, amenity=Amenity.objects.get(key=k))


class ModelSyncTest(TestCase):
    def setUp(self):
        _, self.org = make_developer()

    def test_category_projects_to_property_type(self):
        p = make_property(org=self.org, category='land', subtype='residential_plot')
        p.refresh_from_db()
        self.assertEqual((p.category, p.property_type, p.subtype), ('land', 'plot', 'residential_plot'))

    def test_property_type_only_derives_category(self):
        p = make_property(org=self.org, property_type='plot')
        p.refresh_from_db()
        self.assertEqual(p.category, 'land')

    def test_hospitality_is_kept_with_commercial_alias(self):
        p = make_property(org=self.org, category='hospitality', subtype='hotel')
        p = Property.objects.get(pk=p.pk)
        self.assertEqual(p.property_type, 'commercial')
        p.title = 'renamed'
        p.save()
        p.refresh_from_db()
        self.assertEqual((p.category, p.subtype), ('hospitality', 'hotel'))

    def test_clean_rejects_foreign_subtype(self):
        p = make_property(org=self.org, category='residential')
        p.subtype = 'warehouse'
        with self.assertRaises(ValidationError) as cm:
            p.full_clean()
        self.assertIn('subtype', cm.exception.message_dict)


class SeedTest(TestCase):
    def test_seeded_amenities(self):
        gym = Amenity.objects.get(key='gym')
        self.assertEqual(gym.group, 'building')
        self.assertEqual(gym.label('ur'), 'جم')
        self.assertEqual(gym.label('fr'), 'Gym')                    # falls back to en
        self.assertEqual(Amenity.objects.get(key='servant_quarters').markets, ['PK'])
        self.assertTrue(all(a.labels.get('en') and a.labels.get('ur') for a in Amenity.objects.all()))

    def test_amenity_in_use_is_protected(self):
        from django.db.models import ProtectedError
        _, org = make_developer()
        _amen(make_property(org=org), 'gym')
        with self.assertRaises(ProtectedError):
            Amenity.objects.get(key='gym').delete()


class ApiWriteTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)

    def _create(self, **extra):
        body = {'title': 'H', 'city': 'Lahore', 'location': 'DHA', 'price': 1000, **extra}
        return self.c.post(URL, body, format='json')

    def test_create_and_read_by_key(self):
        r = self._create(category='residential', subtype='house', amenities=['gym', 'lift', 'gym'])
        self.assertEqual(r.status_code, 201, r.data)
        p = Property.objects.get(title='H')
        self.assertEqual((p.property_type, p.subtype), ('residential', 'house'))
        self.assertEqual(sorted(p.amenities.values_list('key', flat=True)), ['gym', 'lift'])
        p.owner = self.dev
        p.save(update_fields=['owner'])
        r = self.c.get(f'{URL}{p.pk}/')
        self.assertEqual(sorted(r.data['amenities']), ['gym', 'lift'])
        self.assertEqual(r.data['category'], 'residential')
        r = self.c.get(URL)
        rows = r.data['results'] if isinstance(r.data, dict) else r.data
        self.assertEqual(sorted(rows[0]['amenities']), ['gym', 'lift'])

    def test_update_replaces_amenity_set(self):
        r = self._create(amenities=['gym'])
        p = Property.objects.get(title='H')
        p.owner = self.dev
        p.save(update_fields=['owner'])
        r = self.c.patch(f'{URL}{p.pk}/', {'amenities': ['cctv', 'lift']}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(sorted(p.amenities.values_list('key', flat=True)), ['cctv', 'lift'])
        r = self.c.patch(f'{URL}{p.pk}/', {'title': 'T2'}, format='json')        # untouched
        self.assertEqual(p.amenities.count(), 2)

    def test_rejections(self):
        cases = [
            ({'amenities': ['sauna_xyz']}, 'amenities'),
            ({'amenities': ['maids_room']}, 'amenities'),                       # AE-only, PK listing
            ({'country': 'AE', 'amenities': ['servant_quarters']}, 'amenities'),  # PK-only
            ({'category': 'land', 'property_type': 'residential'}, 'property_type'),
            ({'category': 'residential', 'subtype': 'warehouse'}, 'subtype'),
            ({'subtype': 'not_a_subtype'}, 'subtype'),
        ]
        for extra, field in cases:
            with self.subTest(extra=extra):
                r = self._create(**extra)
                self.assertEqual(r.status_code, 400, r.data)
                self.assertIn(field, r.data)
        self.assertFalse(Property.objects.filter(title='H').exists())

    def test_inactive_amenity_cannot_be_written(self):
        Amenity.objects.filter(key='gym').update(is_active=False)
        self.assertEqual(self._create(amenities=['gym']).status_code, 400)

    def test_property_type_only_update_derives_category_and_clears_subtype(self):
        self._create(category='residential', subtype='house')
        p = Property.objects.get(title='H')
        p.owner = self.dev
        p.save(update_fields=['owner'])
        r = self.c.patch(f'{URL}{p.pk}/', {'property_type': 'plot'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual((p.category, p.subtype), ('land', None))
        # Category change alone: a stored subtype that no longer fits is cleared.
        p.subtype = 'residential_plot'
        p.save()
        r = self.c.patch(f'{URL}{p.pk}/', {'category': 'commercial'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual((p.category, p.property_type, p.subtype), ('commercial', 'commercial', None))

    def test_subtype_alone_defaults_to_residential(self):
        r = self._create(subtype='apartment')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Property.objects.get(title='H').category, 'residential')


class ListFilterTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)
        self.both = make_property(org=self.org, title='both', category='residential', subtype='house')
        self.gym = make_property(org=self.org, title='gym', category='land')
        self.none = make_property(org=self.org, title='none', category='hospitality', subtype='hotel')
        _amen(self.both, 'gym', 'lift', 'cctv')
        _amen(self.gym, 'gym')

    def _titles(self, query):
        r = self.c.get(URL + query)
        self.assertEqual(r.status_code, 200, r.data)
        rows = r.data['results'] if isinstance(r.data, dict) else r.data
        return sorted(x['title'] for x in rows)

    def test_filters(self):
        self.assertEqual(self._titles('?amenities=gym'), ['both', 'gym'])
        self.assertEqual(self._titles('?amenities=gym,LIFT'), ['both'])
        self.assertEqual(self._titles('?category=land'), ['gym'])
        self.assertEqual(self._titles('?subtype=hotel'), ['none'])
        self.assertEqual(self._titles('?type=commercial'), ['none'])        # deprecated alias
        self.assertEqual(self._titles('?type=plot'), ['gym'])

    def test_invalid_filters(self):
        for q in ('?amenities=gym,nope', '?category=castle', '?subtype=igloo',
                  '?amenities=' + ','.join(Amenity.objects.values_list('key', flat=True)[:11])):
            with self.subTest(q=q):
                self.assertEqual(self.c.get(URL + q).status_code, 400)


class TaxonomyEndpointTest(TestCase):
    def test_taxonomy(self):
        c = APIClient()
        self.assertEqual(c.get(URL + 'taxonomy/').status_code, 401)
        c.force_authenticate(make_user(role='client'))
        r = c.get(URL + 'taxonomy/?market=pk&lang=ur')
        self.assertEqual(r.status_code, 200)
        cats = {x['key']: [s['key'] for s in x['subtypes']] for x in r.data['categories']}
        self.assertEqual(set(cats), set(taxonomy.CATEGORIES))
        self.assertIn('hotel', cats['hospitality'])
        keys = {a['key']: a for a in r.data['amenities']}
        self.assertIn('servant_quarters', keys)
        self.assertNotIn('maids_room', keys)
        self.assertEqual(keys['gym']['label'], 'جم')
        r = c.get(URL + 'taxonomy/?market=AE')
        keys = {a['key'] for a in r.data['amenities']}
        self.assertIn('maids_room', keys)
        self.assertNotIn('servant_quarters', keys)
        self.assertEqual(c.get(URL + 'taxonomy/?market=PAK').status_code, 400)


class SearchServiceTest(TestCase):
    def setUp(self):
        _, self.org = make_developer()
        self.p = make_property(org=self.org, title='with gym', city='Lahore', is_sandbox=False)
        make_property(org=self.org, title='plain', city='Lahore', is_sandbox=False)
        _amen(self.p, 'gym', 'lift')

    def test_db_filter_and_result_keys(self):
        from apps.properties.search import PropertySearchService
        res = PropertySearchService._from_db(city='Lahore', viewer_org=self.org, amenities=('gym',))
        self.assertEqual([r.title for r in res], ['with gym'])
        self.assertEqual(res[0].amenities, ['gym', 'lift'])
        self.assertEqual(PropertySearchService._result_to_dict(res[0])['amenities'], ['gym', 'lift'])

    def test_unknown_amenities_are_soft_and_scrapers_skipped(self):
        from apps.properties.search import _scrapers_apply, resolve_amenities
        self.assertEqual(resolve_amenities('gym, rooftop_helipad'), ('gym',))
        self.assertEqual(resolve_amenities(''), ())
        self.assertFalse(_scrapers_apply('buy', ('gym',)))
        self.assertTrue(_scrapers_apply('buy', ()))

    def test_ai_tool_filters_by_amenity(self):
        from unittest import mock
        from apps.ai._tools_search import search_properties
        from apps.ai._tools_context import _ctx_org
        token = _ctx_org.set(self.org)
        try:
            with mock.patch('apps.properties.search.PropertySearchService._apply_ai_verdicts'):
                out = search_properties(city='Lahore', amenities='gym,unknown_thing')
        finally:
            _ctx_org.reset(token)
        self.assertEqual([p['title'] for p in out['properties']], ['with gym'])
        self.assertEqual(out['properties'][0]['amenities'], ['gym', 'lift'])
        self.assertFalse(out['live_search_pending'])

    def test_search_filter_schema_passes_amenities(self):
        from apps.ai.schemas import PropertySearchFilter
        kw = PropertySearchFilter(city='lahore', amenities=['gym', 'lift']).to_tool_kwargs()
        self.assertEqual(kw['amenities'], 'gym,lift')
