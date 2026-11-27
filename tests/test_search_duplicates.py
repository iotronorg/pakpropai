"""T-4051 — duplicate clusters collapsed in consumer search (FR-LST-010, MVP "de-duplicated", ADR-070)."""
from django.test import override_settings
from rest_framework.test import APIClient

from apps.ai import _tools_listings as T
from apps.ai._tools_context import set_context
from apps.properties import listing_search as S
from apps.properties.models import DuplicateCluster, Property
from tests.factories import make_client, make_org, make_user
from tests.test_listing_search import Base

URL = '/api/v1/search/'
PUBLIC = '/api/v1/public/listings/search/'


class Dup(Base):
    def setUp(self):
        super().setUp()
        self.other = make_org(name='Copycat Realty', operational_mode='production')

    def make(self, title, org='default', **kw):
        if org == 'default':
            return super().make(title, **kw)
        own, self.org = self.org, org
        try:
            return super().make(title, **kw)
        finally:
            self.org = own

    def cluster(self, *listings, canonical=None, status=DuplicateCluster.Status.AUTO):
        c = DuplicateCluster.objects.create(canonical=canonical or listings[0], status=status)
        Property.objects.filter(pk__in=[p.pk for p in listings]).update(duplicate_cluster=c)
        return c

    def ids(self, result):
        return [str(h.listing.pk) for h in result.hits]


class CollapseTest(Dup):
    def test_one_hit_per_cluster_with_the_other_copies(self):
        a = self.make('a', price=10_000_000)
        b = self.make('b', price=10_200_000, org=self.other)
        c = self.make('c', price=9_900_000)
        solo = self.make('solo', price=12_000_000)
        self.cluster(a, b, c)
        r = self.run_search(sort='price_asc')
        self.assertEqual(self.ids(r), [str(c.pk), str(solo.pk)])          # the cheapest copy is shown
        self.assertEqual(r.total, 2)                                       # properties, not listings
        hit = r.hits[0]
        self.assertEqual(hit.also_listed_count, 2)
        self.assertEqual([x['id'] for x in hit.also_listed], [str(a.pk), str(b.pk)])     # by price
        self.assertEqual(hit.also_listed[1]['lister_name'], 'Copycat Realty')
        self.assertEqual((r.hits[1].also_listed, r.hits[1].also_listed_count), ([], 0))
        full = self.run_search(sort='price_asc', collapse_duplicates=False)
        self.assertEqual((len(full.hits), full.total, full.hits[0].also_listed_count), (4, 4, 0))

    def test_shown_copy_follows_the_sort_unless_a_moderator_chose_one(self):
        a = self.make('a', price=10_000_000)
        b = self.make('b', price=10_300_000)
        c = self.cluster(a, b)
        self.assertEqual(self.ids(self.run_search(sort='price_desc')), [str(b.pk)])
        self.assertEqual(self.ids(self.run_search(sort='price_asc')), [str(a.pk)])
        DuplicateCluster.objects.filter(pk=c.pk).update(status=DuplicateCluster.Status.CONFIRMED, canonical=b)
        for sort in S.SORTS:
            self.assertEqual(self.ids(self.run_search(sort=sort)), [str(b.pk)], sort)

    def test_only_matching_visible_copies_take_part(self):
        shown = self.make('shown', price=10_000_000)
        dear = self.make('dear', price=10_400_000)
        hidden = self.make('hidden', price=9_000_000, is_sandbox=True)
        self.cluster(shown, dear, hidden, canonical=hidden, status=DuplicateCluster.Status.CONFIRMED)
        # The canonical copy is not visible: it does not hide the visible ones and is never listed.
        r = self.run_search(sort='price_asc')
        self.assertEqual((self.ids(r), [x['id'] for x in r.hits[0].also_listed]), ([str(shown.pk)], [str(dear.pk)]))
        # A copy outside the filters is not "also listed".
        r = self.run_search({'price': {'max': 10_100_000, 'currency': 'PKR'}})
        self.assertEqual((self.ids(r), r.hits[0].also_listed_count), ([str(shown.pk)], 0))

    def test_cursor_walks_every_property_once(self):
        listings = [self.make(f'p{i}', price=10_000_000 + i * 1000) for i in range(7)]
        self.cluster(listings[0], listings[3], listings[6])
        self.cluster(listings[1], listings[4])
        for sort in S.SORTS:
            seen, cursor = [], ''
            while cursor is not None:
                r = self.run_search(limit=1, cursor=cursor, sort=sort)
                seen += [h.listing.title for h in r.hits]
                cursor = r.next_cursor
            self.assertEqual(seen, [h.listing.title for h in self.run_search(sort=sort).hits], sort)
            self.assertEqual(len(seen), 4, sort)                       # 2 clusters + 2 single listings
        token = self.run_search(limit=1).next_cursor
        with self.assertRaises(S.CursorInvalid):                       # collapsed and full pages never mix
            self.run_search(limit=1, cursor=token, collapse_duplicates=False)

    @override_settings(SEARCH_COLLAPSE_DUPLICATES=False)
    def test_setting_turns_it_off(self):
        a, b = self.make('a'), self.make('b')
        self.cluster(a, b)
        self.assertEqual(len(self.run_search().hits), 2)
        self.assertEqual(len(self.run_search(collapse_duplicates=True).hits), 1)

    def test_hidden_price_does_not_order_the_copies(self):
        shown = self.make('shown', price=9_000_000)
        old = self.make('old', price=10_300_000, price_on_request=True)
        new = self.make('new', price=9_500_000, price_on_request=True)
        mid = self.make('mid', price=10_000_000)
        Property.objects.filter(pk=old.pk).update(created_at=new.created_at.replace(year=2025))
        self.cluster(shown, old, new, mid)
        copies = self.run_search(sort='price_asc').hits[0].also_listed
        self.assertEqual([x['id'] for x in copies], [str(mid.pk), str(old.pk), str(new.pk)])
        self.assertEqual([x['price'] for x in copies], [10_000_000, None, None])

    def test_shown_copies_are_capped(self):
        listings = [self.make(f'p{i}', price=10_000_000 + i) for i in range(S.ALSO_LISTED_SHOWN + 3)]
        self.cluster(*listings)
        hit = self.run_search().hits[0]
        self.assertEqual((len(hit.also_listed), hit.also_listed_count),
                         (S.ALSO_LISTED_SHOWN, S.ALSO_LISTED_SHOWN + 2))


class ApiTest(Dup):
    def test_rest_search(self):
        a, b = self.make('a', price=10_000_000), self.make('b', price=10_100_000, price_on_request=True)
        self.cluster(a, b)
        r = APIClient().post(URL, {'sort': 'price_asc'}, format='json')
        self.assertEqual(r.status_code, 200)
        hit = r.data['results'][0]
        self.assertEqual((r.data['total'], hit['listing']['id'], hit['also_listed_count']), (1, str(a.pk), 1))
        copy = hit['also_listed'][0]
        self.assertEqual((copy['id'], copy['price'], copy['lister_type']), (str(b.pk), None, 'organization'))
        self.assertTrue(copy['slug'].endswith(b.ref_no.lower()))
        for private in ('owner', 'phone', 'organization', 'latitude'):
            self.assertNotIn(private, copy)
        r = APIClient().post(URL, {'collapse_duplicates': False}, format='json')
        self.assertEqual((r.data['total'], len(r.data['results'])), (2, 2))
        r = APIClient().post(URL, {'collapse_duplicates': 'no'}, format='json')
        self.assertEqual((r.status_code, 'collapse_duplicates' in r.data), (400, True))

    def test_public_search(self):
        a, b = self.make('a'), self.make('b')
        self.cluster(a, b)
        api = APIClient()
        r = api.get(PUBLIC)
        self.assertEqual((r.status_code, r.data['total'], r.data['results'][0]['also_listed_count']), (200, 1, 1))
        self.assertEqual(len(api.get(PUBLIC, {'collapse_duplicates': 'false'}).data['results']), 2)
        r = api.get(PUBLIC, {'collapse_duplicates': 'maybe'})
        self.assertEqual((r.status_code, 'collapse_duplicates' in r.data), (400, True))

    def test_private_seller_copy_has_no_name(self):
        seller = make_user(phone='+923001110001')
        a = self.make('a', price=10_000_000)
        b = self.make('b', price=10_100_000, org=None, owner=seller)
        Property.objects.filter(pk=b.pk).update(is_sandbox=False)
        self.cluster(a, b)
        hit = self.run_search(sort='price_asc').hits[0]
        self.assertEqual(str(hit.listing.pk), str(a.pk))
        copy = hit.also_listed[0]
        self.assertEqual((copy['id'], copy['lister_type'], copy['lister_name']), (str(b.pk), 'client', None))


class ToolTest(Dup):
    def test_tool_gives_the_count_only(self):
        user = make_client(phone='+923001235501')
        set_context(user, '923001235501', org=None, message='')
        a, b = self.make('a'), self.make('b', price=10_050_000)
        self.cluster(a, b)
        out = T.search_listings(location='Lahore', purpose='buy')
        self.assertEqual((out['count'], len(out['properties'])), (1, 1))
        row = out['properties'][0]
        self.assertEqual(row['also_listed_count'], 1)
        self.assertNotIn('also_listed', row)
        self.assertIn('duplicates_note', out)
