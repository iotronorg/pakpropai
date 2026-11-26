"""T-3060 — listing analytics: impressions, detail views, saves, enquiries (FR-LST-012, ADR-058)."""
from datetime import date, datetime, timedelta
from datetime import timezone as dt_tz
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db import DatabaseError, connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.organizations.models import OrganizationMembership
from apps.properties import analytics as A
from apps.properties.models import ListingDailyStat, Property
from tests.factories import make_agent, make_client, make_developer, make_membership, make_org, make_property, make_user
from tests.test_runtime import LOCMEM

PUB = '/api/v1/public/listings/'
REPORT = '/api/v1/reports/listing-performance/'
PKT = ZoneInfo('Asia/Karachi')
BROWSER = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1'


@override_settings(CACHES=LOCMEM, EMAIL_HOST='')
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime', operational_mode='production'))
        self.ua, self.agent = make_agent(org=self.org, name='Agent A', is_active=True)
        self.p = self.live(make_property(org=self.org, title='Corner House', bedrooms=3))
        self.q = self.live(make_property(org=self.org, title='Garden Flat', bedrooms=2))
        self.buyer = make_client()

    def live(self, p, **kw):
        Property.objects.filter(pk=p.pk).update(is_sandbox=False, **kw)
        p.refresh_from_db()
        return p

    def api(self, user=None, **headers):
        c = APIClient(**({'HTTP_USER_AGENT': BROWSER} | headers))
        if user:
            c.force_authenticate(user)
        return c

    def stat(self, p, field):
        return sum(ListingDailyStat.objects.filter(listing=p).values_list(field, flat=True))


class CountingTest(Base):
    def test_search_results_count_as_impressions_in_one_write(self):
        with CaptureQueriesContext(connection) as q:
            r = self.api().get(PUB + 'search/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual((self.stat(self.p, 'impressions'), self.stat(self.q, 'impressions')), (1, 1))
        self.assertEqual(sum('listing_daily_stats' in x['sql'] for x in q.captured_queries), 1)
        self.api().post('/api/v1/search/', {}, format='json')
        self.assertEqual(self.stat(self.p, 'impressions'), 2)

    def test_bots_are_not_counted(self):
        for ua in ('Googlebot/2.1 (+http://www.google.com/bot.html)', 'facebookexternalhit/1.1', 'curl/8.4.0',
                   'python-requests/2.32'):
            c = self.api(HTTP_USER_AGENT=ua)
            self.assertEqual(c.get(PUB + 'search/').status_code, 200)
            self.assertEqual(c.get(f'{PUB}{self.p.pk}/').status_code, 200)
        self.assertFalse(ListingDailyStat.objects.exists())

    def test_detail_view_once_per_visitor_per_day(self):
        for _ in range(3):
            self.assertEqual(self.api().get(f'{PUB}{self.p.pk}/').status_code, 200)
        self.assertEqual(self.stat(self.p, 'detail_views'), 1)
        self.api(REMOTE_ADDR='10.0.0.9').get(f'{PUB}{self.p.ref_no}/')       # another visitor
        self.assertEqual(self.stat(self.p, 'detail_views'), 2)
        self.assertEqual(self.api().get(f'{PUB}nope/').status_code, 404)
        self.assertEqual(ListingDailyStat.objects.count(), 1)

    def test_no_address_is_stored(self):
        self.api(REMOTE_ADDR='203.0.113.77').get(f'{PUB}{self.p.pk}/')
        row = ListingDailyStat.objects.values().get()
        self.assertEqual(set(row), {'id', 'listing_id', 'day', 'impressions', 'detail_views', 'saves', 'enquiries'})
        seen = [k for k in cache._cache if 'listing-view' in k]
        self.assertEqual(len(seen), 1)
        self.assertNotIn('203.0.113.77', seen[0])                       # only a keyed hash

    def test_signed_in_detail_counts_people_outside_the_listing(self):
        self.api(self.buyer).get(f'/api/v1/properties/{self.p.pk}/')
        self.api(self.buyer).get(f'/api/v1/properties/{self.p.pk}/')
        self.assertEqual(self.stat(self.p, 'detail_views'), 1)
        for insider in (self.owner, self.ua):
            self.assertEqual(self.api(insider).get(f'/api/v1/properties/{self.p.pk}/').status_code, 200)
        self.assertEqual(self.stat(self.p, 'detail_views'), 1)

    def test_own_listings_in_search_are_not_impressions(self):
        self.api(self.ua).post('/api/v1/search/', {}, format='json')
        self.assertEqual(self.stat(self.p, 'impressions'), 0)
        other = self.live(make_property(owner=make_client(), title='Private flat', bedrooms=1))
        self.api(other.owner).post('/api/v1/search/', {}, format='json')
        self.assertEqual((self.stat(self.p, 'impressions'), self.stat(other, 'impressions')), (1, 0))

    def test_new_saves_only(self):
        c = self.api(self.buyer)
        with self.captureOnCommitCallbacks(execute=True):
            c.post('/api/v1/me/favourites/', {'listing_id': str(self.p.pk)}, format='json')
        with self.captureOnCommitCallbacks(execute=True):
            c.post('/api/v1/me/favourites/', {'listing_id': str(self.p.pk), 'note': 'nice'}, format='json')
        self.assertEqual(self.stat(self.p, 'saves'), 1)
        with self.captureOnCommitCallbacks(execute=True):
            c.post('/api/v1/me/favourites/', {'listing_id': str(self.q.pk), 'kind': 'dismissed'}, format='json')
        self.assertEqual(self.stat(self.q, 'saves'), 0)
        with self.captureOnCommitCallbacks(execute=True):
            c.post('/api/v1/me/favourites/', {'listing_id': str(self.q.pk), 'kind': 'saved'}, format='json')
        self.assertEqual(self.stat(self.q, 'saves'), 1)

    def test_new_enquiry_threads_only(self):
        body = {'listing_id': str(self.p.pk), 'message': 'Is it available?', 'contact_consent': True}
        for _ in range(2):                                   # the second continues the open thread
            with self.captureOnCommitCallbacks(execute=True):
                self.assertIn(self.api(self.buyer).post('/api/v1/enquiries/', body, format='json').status_code,
                              (200, 201))
        self.assertEqual(self.stat(self.p, 'enquiries'), 1)

    def test_failed_write_does_not_break_the_page(self):
        with patch('apps.properties.analytics.connection') as conn:
            conn.cursor.side_effect = DatabaseError('down')
            self.assertEqual(self.api().get(PUB + 'search/').status_code, 200)
            self.assertEqual(self.api().get(f'{PUB}{self.p.pk}/').status_code, 200)
        self.assertFalse(ListingDailyStat.objects.exists())

    def test_cache_outage_still_counts(self):
        with patch('django.core.cache.cache.add', side_effect=ConnectionError('redis')):
            self.assertEqual(self.api().get(f'{PUB}{self.p.pk}/').status_code, 200)
        self.assertEqual(self.stat(self.p, 'detail_views'), 1)

    def test_day_is_the_markets_local_day(self):
        late = datetime(2026, 11, 25, 20, 30, tzinfo=dt_tz.utc)        # 01:30 on the 26th in Karachi
        with patch('apps.properties.analytics.timezone.now', return_value=late):
            A.record(A.IMPRESSIONS, [self.p, self.p])
        self.assertEqual(list(ListingDailyStat.objects.values_list('day', 'impressions')), [(date(2026, 11, 26), 1)])
        with self.assertRaises(ValueError):
            A.record('price; drop table x', [self.p])

    def test_assistant_views(self):
        A.shown(None, [self.p])
        A.detail_viewed_in_chat(self.buyer, self.p)
        A.detail_viewed_in_chat(self.buyer, self.p)
        A.detail_viewed_in_chat(self.ua, self.p)                       # the listing's own agent
        A.detail_viewed_in_chat(None, self.p)
        self.assertEqual((self.stat(self.p, 'impressions'), self.stat(self.p, 'detail_views')), (1, 1))


class ReportTest(Base):
    def seed(self, p, days_ago, **counts):
        day = timezone.now().astimezone(PKT).date()
        ListingDailyStat.objects.create(listing=p, day=day - timedelta(days=days_ago), **counts)

    def get(self, user, **params):
        return self.api(user).get(REPORT, params)

    def test_manager_sees_every_listing_with_rates(self):
        self.seed(self.p, 1, impressions=200, detail_views=40, saves=4, enquiries=2)
        self.seed(self.p, 3, impressions=100, detail_views=10, saves=1, enquiries=3)
        self.seed(self.q, 2, impressions=50)
        self.seed(self.p, 40, impressions=9999)                       # outside the default 30 days
        r = self.get(self.owner)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 2)
        first = r.data['listings'][0]
        self.assertEqual((first['listing_id'], first['impressions'], first['detail_views'], first['saves'],
                          first['enquiries']), (str(self.p.pk), 300, 50, 5, 5))
        self.assertEqual((first['view_rate_pct'], first['save_rate_pct'], first['enquiry_rate_pct']),
                         (16.7, 10.0, 10.0))
        self.assertEqual(r.data['totals']['impressions'], 350)
        self.assertEqual(r.data['timezone'], 'Asia/Karachi')
        self.assertEqual(self.get(self.owner, sort='enquiries', limit=1, offset=1).data['listings'][0]['listing_id'],
                         str(self.q.pk))

    def test_live_listings_without_activity_show_zeros(self):
        r = self.get(self.owner)
        self.assertEqual(r.data['count'], 2)
        self.assertEqual(r.data['listings'][0]['view_rate_pct'], None)
        archived = self.live(make_property(org=self.org, title='Old'), status='archived', is_active=False)
        self.assertEqual(self.get(self.owner).data['count'], 2)
        self.seed(archived, 5, impressions=3)                           # had activity in the period
        self.assertEqual(self.get(self.owner).data['count'], 3)

    def test_agent_sees_assigned_listings_only(self):
        Property.objects.filter(pk=self.p.pk).update(assigned_agent=self.agent)
        r = self.get(self.ua)
        self.assertEqual([x['listing_id'] for x in r.data['listings']], [str(self.p.pk)])
        self.assertEqual(self.get(self.ua, listing=str(self.q.pk)).status_code, 404)

    def test_private_seller_and_strangers(self):
        seller = make_client()
        own = self.live(make_property(owner=seller, title='My flat'))
        self.seed(own, 0, detail_views=7)
        r = self.get(seller)
        self.assertEqual(([x['listing_id'] for x in r.data['listings']], r.data['totals']['detail_views']),
                         ([str(own.pk)], 7))
        self.assertEqual(self.get(self.buyer).data['count'], 0)
        self.assertEqual(self.get(self.buyer, listing=str(own.pk)).status_code, 404)
        rival, _ = make_developer(org=make_org(name='Rival', operational_mode='production'))
        self.assertEqual(self.get(rival).data['count'], 0)
        self.assertEqual(self.get(rival, listing=str(self.p.pk)).status_code, 404)
        viewer = make_user(role='agent')
        make_membership(viewer, self.org, role=OrganizationMembership.Role.VIEWER)
        self.assertEqual(self.get(viewer).data['count'], 0)
        self.assertEqual(APIClient().get(REPORT).status_code, 401)

    def test_daily_series_for_one_listing(self):
        self.seed(self.p, 1, impressions=5)
        today = timezone.now().astimezone(PKT).date()
        r = self.get(self.owner, listing=str(self.p.pk), **{'from': (today - timedelta(days=2)).isoformat(),
                                                            'to': today.isoformat()})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([d['impressions'] for d in r.data['daily']], [0, 5, 0])
        self.assertEqual(r.data['count'], 1)

    def test_admin_names_the_organization(self):
        admin = make_user(role='admin')
        self.assertEqual(self.get(admin).status_code, 400)
        self.assertEqual(self.get(admin, organization=str(self.org.pk)).data['count'], 2)
        self.assertEqual(self.get(admin, listing=str(self.p.pk)).data['count'], 1)

    def test_parameters_are_checked(self):
        for params in ({'sort': 'price'}, {'limit': '0'}, {'limit': '500'}, {'offset': '-1'}, {'limit': 'x'},
                       {'listing': 'nope'}, {'from': '2026-13-01'}, {'from': '2026-11-20', 'to': '2026-11-01'},
                       {'from': '2024-01-01', 'to': '2026-01-01'}):
            self.assertEqual(self.get(self.owner, **params).status_code, 400, params)
