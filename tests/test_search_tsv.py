"""
T-1070f (KI-035) — stored search_tsv: trigger keeps it equal to the
search_vector() expression for every kind of write, the backfill fills NULLs,
and ?search= uses it with unchanged results and ranking.
"""
import importlib

from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.properties.models import Property
from apps.properties.text_search import apply_text_search, search_vector
from tests.factories import make_client, make_org, make_property


def _matches_expression(pk) -> bool:
    """Stored document equals the legacy expression for this row."""
    stored, expr = (Property.objects.filter(pk=pk).annotate(expr=search_vector())
                    .values_list('search_tsv', 'expr').get())
    return stored is not None and str(stored) == str(expr)


class TriggerTest(TestCase):
    def setUp(self):
        self.org = make_org(operational_mode='production')

    def test_insert_and_save(self):
        p = make_property(org=self.org, title='Corner villa', description='Near park')
        self.assertTrue(_matches_expression(p.pk))
        p = Property.objects.get(pk=p.pk)
        p.description = 'Sea view penthouse'
        p.save()
        self.assertTrue(_matches_expression(p.pk))
        self.assertEqual(apply_text_search(Property.objects.all(), 'penthouse').count(), 1)

    def test_bypass_writes_and_unrelated_updates(self):
        p = make_property(org=self.org, title='Old title')
        Property.objects.filter(pk=p.pk).update(title='Brand new duplex')     # no save()
        self.assertTrue(_matches_expression(p.pk))
        p = Property.objects.get(pk=p.pk)
        p.ai_score = 70
        p.save(update_fields=['ai_score'])                                    # trigger not fired
        self.assertTrue(_matches_expression(p.pk))
        with connection.cursor() as cur:                                      # raw SQL tampering
            cur.execute("UPDATE properties SET search_tsv = to_tsvector('simple', 'junk') WHERE id = %s",
                        [p.pk])
        self.assertTrue(_matches_expression(p.pk))

    def test_stale_in_memory_value_is_recomputed_on_full_save(self):
        a = Property.objects.get(pk=make_property(org=self.org, title='Alpha').pk)
        Property.objects.filter(pk=a.pk).update(title='Bravo')
        a.location = 'Gulberg'                                                # a still says Alpha
        a.save()
        self.assertTrue(_matches_expression(a.pk))


class BackfillTest(TestCase):
    def test_backfill_fills_nulls(self):
        org = make_org(operational_mode='production')
        pks = [make_property(org=org, location=f'L{i}').pk for i in range(3)]
        with connection.cursor() as cur:
            cur.execute('SET session_replication_role = replica')        # triggers off
            try:
                cur.execute('UPDATE properties SET search_tsv = NULL')
            finally:
                cur.execute('SET session_replication_role = DEFAULT')
        self.assertEqual(Property.objects.filter(search_tsv__isnull=True).count(), 3)
        mig = importlib.import_module('apps.properties.migrations.0036_backfill_search_tsv')
        mig.backfill(None, connection.schema_editor())
        self.assertEqual(Property.objects.filter(search_tsv__isnull=True).count(), 0)
        self.assertTrue(all(_matches_expression(pk) for pk in pks))


def _legacy_search(term):
    """The pre-T-1070f query (expression document), for parity checks."""
    from django.contrib.postgres.search import SearchQuery, SearchRank
    from apps.properties.text_search import SEARCH_CONFIG, build_prefix_tsquery
    q = SearchQuery(build_prefix_tsquery(term), search_type='raw', config=SEARCH_CONFIG)
    return list(Property.objects.alias(d=search_vector()).filter(d=q)
                .annotate(rank=SearchRank(search_vector(), q))
                .order_by('-rank', '-created_at').values_list('title', flat=True))


class ApiParityTest(TestCase):
    def test_same_results_and_order_as_legacy_query(self):
        org = make_org(operational_mode='production')
        make_property(org=org, title='Apartment apartment', description='apartment gulberg')
        make_property(org=org, title='Apartment', location='Gulberg III')
        make_property(org=org, title='Gulberg house', city='Lahore')
        make_property(org=org, title='House', location='DHA')
        for term in ('apart', 'gulberg', 'apartment gulberg', 'lahore house', 'nothing'):
            with self.subTest(term=term):
                new = list(apply_text_search(Property.objects.all(), term)
                           .values_list('title', flat=True))
                self.assertEqual(new, _legacy_search(term))
        c = APIClient()
        c.force_authenticate(make_client())
        r = c.get('/api/v1/properties/?search=gulberg')
        self.assertEqual(r.status_code, 200)
        rows = r.data['results'] if isinstance(r.data, dict) else r.data
        self.assertEqual([x['title'] for x in rows], _legacy_search('gulberg'))
