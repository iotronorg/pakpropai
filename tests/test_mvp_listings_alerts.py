"""MVP acceptance 3 (only public listings are shown) and 5 (alerts arrive within their frequency)."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.conversations.views import cards
from apps.leads.models import Lead
from apps.markets.models import Market
from apps.preferences import alerts as A
from apps.preferences.models import AlertDelivery, SavedSearch
from apps.properties import listing_search as LS
from apps.properties import recommendations as R
from apps.properties.models import Property
from apps.properties.search import PropertySearchService
from apps.reports.models import Report
from apps.whatsapp.discovery_engine import GeoContextResolver
from apps.whatsapp.production_provisioner import SandboxDataMigrator
from tests.factories import make_agent, make_client, make_developer, make_lead, make_org, make_property
from tests.test_api_key_security import _make_key
from tests.test_preferences import CRIT, Base
from tests.test_runtime import LOCMEM

API = '/api/v1/properties/'
HIDDEN = {'expired': {'expires_at': timezone.now() - timedelta(minutes=1)},
          'no market, inactive country': {'market_id': None, 'country': 'GB'},
          'inactive market': {'market_id': 'GB'},
          'sandbox': {'is_sandbox': True},
          'draft': {'status': 'draft', 'is_active': False},
          'suspended': {'status': 'suspended', 'is_active': False},
          'sold': {'status': 'sold', 'is_active': False}}


@override_settings(CACHES=LOCMEM)
class OnlyPublicListingsTest(TestCase):
    """The same seven hidden listings and one visible listing, read through every consumer surface."""

    def setUp(self):
        cache.clear()
        Market.objects.update_or_create(code='GB', defaults={'name': 'United Kingdom', 'status': 'inactive'})
        self.org = make_org(operational_mode='production', name='Prime')
        self.bad = {name: self.make(name, **kw) for name, kw in HIDDEN.items()}
        self.ok = self.make('visible')
        self.client_user = make_client()
        self.lead = make_lead(make_client(), org=make_org(name='Other', operational_mode='production'))

    def make(self, title, **kw):
        p = make_property(org=self.org, title=title, price=10_000_000, city='Lahore', bedrooms=3)
        Property.objects.filter(pk=p.pk).update(**{'is_sandbox': False, **kw})
        p.refresh_from_db()
        return p

    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_queries(self):
        only = {self.ok.pk}
        self.assertEqual(set(Property.objects.visible_to(None).values_list('pk', flat=True)), only)
        everyone = [str(p.pk) for p in (*self.bad.values(), self.ok)]
        self.assertEqual([c['id'] for c in cards(everyone)], [str(self.ok.pk)])
        self.assertEqual([p for p in everyone if LS.find(p)], [str(self.ok.pk)])
        self.assertEqual({r.title for r in PropertySearchService._from_db(city='Lahore')}, {'visible'})

    def test_recommendations(self):
        lead = self.lead
        lead.organization, lead.city_interest, lead.intent = self.org, 'Lahore', 'buy'
        Lead.objects.filter(pk=lead.pk).update(organization=self.org)
        self.assertEqual({p.pk for p in R.recommend_for_lead(lead, 20)} - {self.bad['sandbox'].pk}, {self.ok.pk})
        other = self.make('another')
        self.assertEqual({p.pk for p in R.similar_to(other, 20)} - {self.bad['sandbox'].pk}, {self.ok.pk})

    def test_rest_list_and_detail_for_a_consumer(self):
        c = self.api(self.client_user)
        r = c.get(API)
        self.assertEqual({x['id'] for x in r.data['results']}, {str(self.ok.pk)})
        self.assertEqual(c.get(f'{API}{self.ok.pk}/').status_code, 200)
        for name, p in self.bad.items():
            for path in (f'{API}{p.pk}/', f'/api/v1/public/listings/{p.pk}/'):
                self.assertEqual(c.get(path).status_code, 404, (name, path))
        mine = make_property(owner=self.client_user, title='mine')
        Property.objects.filter(pk=mine.pk).update(expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(c.get(f'{API}{mine.pk}/').status_code, 200)

    def test_compare_and_trends_for_an_agent_without_an_organization(self):
        agent, _ = make_agent()
        c = self.api(agent)
        ids = ','.join(str(p.pk) for p in (self.bad['sandbox'], self.bad['expired'], self.ok))
        self.assertEqual([x['id'] for x in c.get(f'{API}compare/?ids={ids}').data['results']], [str(self.ok.pk)])
        self.assertEqual(sum(x['count'] for x in c.get(f'{API}market-trends/').data['results']), 1)

    def test_location_pin_on_whatsapp(self):
        from unittest.mock import MagicMock
        with patch.object(GeoContextResolver, '_nominatim_lookup', return_value='Lahore'):
            rows = GeoContextResolver.resolve(31.5, 74.3, self.org, MagicMock(), MagicMock(context={}))
        self.assertEqual({p.pk for p in rows} - {self.bad['sandbox'].pk}, {self.ok.pk})

    def test_reports_only_on_listings_the_person_may_see(self):
        def ask(user, pid):
            cache.clear()                                     # hourly limit
            with patch('apps.reports.tasks.generate_report_task.delay', create=True):
                return self.api(user).post('/api/v1/reports/generate/', {
                    'report_type': 'fraud_check', 'property_id': pid}, format='json').status_code
        for p in self.bad.values():
            self.assertEqual(ask(self.client_user, str(p.pk)), 404, p.title)
        self.assertEqual(ask(self.client_user, 'not-a-uuid'), 404)
        self.assertEqual(Report.objects.count(), 0)
        self.assertEqual(ask(self.client_user, str(self.ok.pk)), 202)

    def test_recommended_is_not_for_consumers(self):
        self.assertEqual(self.api(self.client_user).get(f'{API}recommended/?lead={self.lead.pk}').status_code, 404)

    def test_owner_contact_is_shown_only_to_those_who_manage_the_listing(self):
        seller, (dev, org) = make_client(), make_developer()
        private = make_property(owner=seller, title='private')
        Property.objects.filter(pk=private.pk).update(is_sandbox=False, raw_docs={'deed': 'x'})
        data = self.api(self.client_user).get(f'{API}{private.pk}/').data
        self.assertEqual([data[k] for k in ('owner_phone', 'raw_docs', 'ai_analysis')], [None] * 3)
        data = self.api(seller).get(f'{API}{private.pk}/').data
        self.assertEqual((data['owner_phone'], data['raw_docs']), (seller.phone, {'deed': 'x'}))
        theirs = make_property(org=org, title='theirs')
        Property.objects.filter(pk=theirs.pk).update(raw_docs={'deed': 'y'})
        self.assertEqual(self.api(dev).get(f'{API}{theirs.pk}/').data['raw_docs'], {'deed': 'y'})


class PartnerApiTest(TestCase):
    def test_inventory_lists_the_organizations_own_listings(self):
        org = make_org(operational_mode='production')
        mine = make_property(org=org, title='mine')
        make_property(org=make_org(name='Other', operational_mode='production'), title='theirs')
        key, _ = _make_key(org, scopes=['inventory:read'])
        r = APIClient().get('/api/v1/external/inventory/', HTTP_X_API_KEY=key)
        self.assertEqual((r.status_code, [x['id'] for x in r.data]), (200, [str(mine.pk)]))


class AlertFrequencyTest(Base):
    """Beat runs every 15 minutes: no match waits longer than its frequency plus one run."""

    def simulate(self, frequency, days, listed_at):
        start = timezone.now() + timedelta(days=1)
        with patch('apps.preferences.alerts.timezone.now', return_value=start):
            A.create(self.user, CRIT, frequency=frequency)
        published = {}
        for i, minutes in enumerate(listed_at):
            at = start + timedelta(minutes=minutes)
            published[self.make(f'L{i}', published_at=at).pk] = at
        Property.objects.filter(pk__in=published).update(is_active=False)
        # alerts see only what is public at that moment
        told, tick = {}, start
        while tick <= start + timedelta(days=days):
            Property.objects.filter(pk__in=[k for k, at in published.items() if at <= tick]).update(is_active=True,
                                                                                                  status='published')
            if SavedSearch.objects.filter(next_run_at__lte=tick).exists():
                with patch('apps.preferences.alerts.timezone.now', return_value=tick):
                    A.run_due()
                for pk in AlertDelivery.objects.values_list('listing_id', flat=True):
                    told.setdefault(pk, tick)
            tick += timedelta(minutes=15)
        self.assertEqual(set(told), set(published))
        return max(told[k] - published[k] for k in published)

    def test_instant(self):
        self.assertLessEqual(self.simulate('instant', 1, (1, 16, 50, 200, 201)), timedelta(hours=1, minutes=15))

    def test_daily(self):
        self.assertLessEqual(self.simulate('daily', 4, (1, 700, 1441, 2000, 4000)), timedelta(days=1, minutes=15))

    def test_weekly(self):
        self.assertLessEqual(self.simulate('weekly', 22, (1, 10081, 20000)), timedelta(days=7, minutes=15))

    def test_listings_of_an_organization_that_goes_live_are_announced(self):
        org = make_org(name='Starting')
        p = self.make('Built in the sandbox', is_sandbox=True, published_at=timezone.now() - timedelta(days=9))
        Property.objects.filter(pk=p.pk).update(organization=org)
        A.create(self.user, CRIT, frequency='daily')
        SandboxDataMigrator().migrate(org)
        SavedSearch.objects.update(next_run_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(A.run_due(), 1)
        self.assertEqual(AlertDelivery.objects.get().listing_id, p.pk)

    def test_more_than_one_batch_is_never_lost(self):
        A.create(self.user, CRIT, frequency='daily')
        with patch.object(A, 'BATCH', 2):
            for i in range(3):
                self.make(f'L{i}')
            for _ in range(2):
                SavedSearch.objects.update(next_run_at=timezone.now() - timedelta(seconds=1))
                A.run_due()
        self.assertEqual(AlertDelivery.objects.count(), 3)

    def test_a_backlog_is_logged(self):
        A.create(self.user, CRIT, frequency='daily')
        SavedSearch.objects.update(next_run_at=timezone.now() - timedelta(seconds=1))
        with self.assertLogs('apps.preferences.alerts', 'WARNING'):
            A.run_due(limit=1)
