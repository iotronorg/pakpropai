"""
ADR-002 — sandbox data integrity and consumer listing visibility.

Covers:
  * is_sandbox is derived on insert (property / lead / WhatsApp session)
  * Property.objects.visible_to() rules
  * PropertySearchService and the AI search tool never leak another org's
    sandbox inventory, but an org sees its own sandbox listings
  * client REST listing API hides other orgs' sandbox listings
  * recommendations respect visibility
  * the backfill migration logic
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.leads.models import Lead
from apps.properties.models import Property
from apps.properties.search import PropertySearchService, SearchParams
from apps.whatsapp.models import WhatsAppSession
from tests.factories import make_client, make_lead, make_org, make_property


def _prod_org(name='Prod Org'):
    return make_org(name=name, operational_mode='production')


def _sandbox_org(name='Sandbox Org'):
    return make_org(name=name)  # default operational_mode = sandbox


class DeriveOnInsertTest(TestCase):

    def test_production_org_listing_is_live(self):
        prop = make_property(org=_prod_org())
        self.assertFalse(prop.is_sandbox)

    def test_sandbox_org_listing_is_sandbox(self):
        prop = make_property(org=_sandbox_org())
        self.assertTrue(prop.is_sandbox)

    def test_platform_listing_is_sandbox(self):
        prop = make_property()  # no org, no owner → platform
        self.assertEqual(prop.listing_owner_type, 'platform')
        self.assertTrue(prop.is_sandbox)

    def test_private_client_listing_is_live(self):
        prop = make_property(owner=make_client())
        self.assertFalse(prop.is_sandbox)

    def test_flag_not_recomputed_on_update(self):
        org = _sandbox_org()
        prop = make_property(org=org)
        org.operational_mode = 'production'
        org.save(update_fields=['operational_mode'])
        prop.title = 'Renamed'
        prop.save()
        prop.refresh_from_db()
        # Go-live migration is the provisioner's job, not an incidental save.
        self.assertTrue(prop.is_sandbox)

    def test_lead_of_production_org_is_live(self):
        lead = make_lead(make_client(), org=_prod_org())
        self.assertFalse(lead.is_sandbox)

    def test_lead_of_sandbox_org_is_sandbox(self):
        lead = make_lead(make_client(), org=_sandbox_org())
        self.assertTrue(lead.is_sandbox)

    def test_unscoped_lead_keeps_default(self):
        lead = make_lead(make_client())
        self.assertTrue(lead.is_sandbox)

    def test_session_of_production_org_is_live(self):
        session = WhatsAppSession.objects.create(phone='+923001234500', organization=_prod_org())
        self.assertFalse(session.is_sandbox)


class VisibleToTest(TestCase):

    def setUp(self):
        self.prod = _prod_org('P')
        self.sandbox = _sandbox_org('S')
        self.live = make_property(org=self.prod, title='live')
        self.sbx = make_property(org=self.sandbox, title='sbx')
        self.demo = make_property(title='demo')
        self.inactive = make_property(org=self.prod, title='inactive', is_active=False)

    def _titles(self, qs):
        return set(qs.values_list('title', flat=True))

    def test_anonymous_viewer_sees_only_live(self):
        self.assertEqual(self._titles(Property.objects.visible_to(None)), {'live'})

    def test_sandbox_org_sees_own_sandbox_plus_live(self):
        self.assertEqual(self._titles(Property.objects.visible_to(self.sandbox)), {'live', 'sbx'})

    def test_accepts_org_id(self):
        self.assertEqual(self._titles(Property.objects.visible_to(self.sandbox.id)), {'live', 'sbx'})

    def test_other_org_cannot_see_foreign_sandbox(self):
        other = _sandbox_org('Other')
        self.assertEqual(self._titles(Property.objects.visible_to(other)), {'live'})


@patch('apps.properties.search.PropertySearchService._from_scrapers', return_value=[])
@patch('apps.properties.search.PropertySearchService._apply_ai_verdicts')
class SearchServiceVisibilityTest(TestCase):

    def setUp(self):
        self.prod = _prod_org('P')
        self.sandbox = _sandbox_org('S')
        # Distinct location/price/size so search de-duplication keeps all rows.
        make_property(org=self.prod, title='live', city='Lahore', location='DHA', price=5_000_000)
        make_property(org=self.sandbox, title='sbx', city='Lahore', location='Gulberg',
                      price=9_000_000, area_marla=10)
        make_property(title='demo', city='Lahore', location='Johar', price=12_000_000, area_marla=20)

    def test_search_without_org_returns_only_live(self, *_):
        results = PropertySearchService.search(city='Lahore')
        self.assertEqual({r.title for r in results}, {'live'})

    def test_search_with_sandbox_org_context_includes_own(self, *_):
        results = PropertySearchService.search(city='Lahore', viewer_org=self.sandbox)
        self.assertEqual({r.title for r in results}, {'live', 'sbx'})

    def test_execute_respects_viewer_org_id(self, *_):
        out = PropertySearchService.execute(SearchParams(city='Lahore'))
        self.assertEqual({r['title'] for r in out['results']}, {'live'})
        out = PropertySearchService.execute(
            SearchParams(city='Lahore', viewer_org_id=str(self.sandbox.id), page_size=10),
        )
        self.assertEqual({r['title'] for r in out['results']}, {'live', 'sbx'})

    def test_ai_tool_uses_org_context(self, *_):
        from apps.ai import tools as tool_module
        from apps.ai._tools_search import search_properties

        tool_module.set_context(None, '+923000000001', org=None)
        self.assertEqual({p['title'] for p in search_properties(city='Lahore')['properties']}, {'live'})

        tool_module.set_context(None, '+923000000001', org=self.sandbox)
        self.assertEqual(
            {p['title'] for p in search_properties(city='Lahore')['properties']}, {'live', 'sbx'},
        )


class ClientApiVisibilityTest(TestCase):

    def test_client_list_hides_foreign_sandbox_but_shows_own_listing(self):
        client_user = make_client()
        make_property(org=_prod_org('P'), title='live')
        make_property(org=_sandbox_org('S'), title='sbx')
        make_property(title='demo')
        own = make_property(owner=client_user, title='mine')
        # Simulate a legacy row whose flag was never derived.
        Property.objects.filter(pk=own.pk).update(is_sandbox=True)

        api = APIClient()
        api.force_authenticate(client_user)
        resp = api.get('/api/v1/properties/')
        self.assertEqual(resp.status_code, 200)
        titles = {row['title'] for row in resp.json()['results']}
        self.assertEqual(titles, {'live', 'mine'})


class RecommendationVisibilityTest(TestCase):

    def test_similar_to_excludes_foreign_sandbox(self):
        from apps.properties.recommendations import similar_to

        prod = _prod_org('P')
        base = make_property(org=prod, title='base', city='Lahore')
        make_property(org=prod, title='live-sibling', city='Lahore')
        make_property(org=_sandbox_org('S'), title='sbx', city='Lahore')
        titles = {p.title for p in similar_to(base)}
        self.assertIn('live-sibling', titles)
        self.assertNotIn('sbx', titles)


class BackfillMigrationTest(TestCase):

    def test_backfill_recomputes_flags(self):
        import importlib

        from django.apps import apps as django_apps

        backfill = importlib.import_module(
            'apps.properties.migrations.0020_backfill_is_sandbox'
        ).backfill

        prod = _prod_org('P')
        sandbox = _sandbox_org('S')
        live = make_property(org=prod)
        sbx = make_property(org=sandbox)
        demo = make_property()
        private = make_property(owner=make_client())
        # Corrupt the flags the way the old code left them.
        Property.objects.filter(pk__in=[live.pk, private.pk]).update(is_sandbox=True)
        Property.objects.filter(pk__in=[sbx.pk, demo.pk]).update(is_sandbox=False)

        backfill(django_apps, None)

        flags = dict(Property.objects.values_list('pk', 'is_sandbox'))
        self.assertFalse(flags[live.pk])
        self.assertFalse(flags[private.pk])
        self.assertTrue(flags[sbx.pk])
        self.assertTrue(flags[demo.pk])

    def test_lead_backfill(self):
        import importlib

        from django.apps import apps as django_apps

        backfill = importlib.import_module('apps.leads.migrations.0019_backfill_is_sandbox').backfill
        prod = _prod_org('P')
        lead = make_lead(make_client(), org=prod)
        Lead.objects.filter(pk=lead.pk).update(is_sandbox=True)
        backfill(django_apps, None)
        lead.refresh_from_db()
        self.assertFalse(lead.is_sandbox)
