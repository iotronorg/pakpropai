"""
T-1100 / ADR-015 — listing search indexes.

Covers:
  * the indexes and the pg_trgm extension exist after migrating
  * the planner can use each index for the query shape the code really emits
    (expression indexes are only used when the query repeats the expression,
    so a refactor that changes the SQL must fail here, not silently in prod)
  * the full-text helper: tokenising, operator stripping, caps, prefix match
  * API ?search= behaviour: prefix/AND matching, ranking, visibility kept,
    junk input → empty result instead of an error
  * the migration builds the indexes concurrently
"""
import importlib

from django.db import connection, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.properties.models import Property
from apps.properties.text_search import (
    MAX_TOKENS, apply_text_search, build_prefix_tsquery,
)
from tests.factories import make_client, make_org, make_property

NEW_INDEXES = {
    'prop_fts_gin', 'prop_city_trgm', 'prop_location_trgm', 'prop_active_rank_idx',
    'prop_search_tsv_gin',
}


def _prod_org(name='Prod Org'):
    return make_org(name=name, operational_mode='production')


def _plan(qs) -> str:
    """EXPLAIN with sequential scans disabled, so a usable index is always chosen."""
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute('SET LOCAL enable_seqscan = off')
        return qs.explain()


class IndexPresenceTest(TestCase):

    def test_indexes_exist(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'properties'"
            )
            defs = dict(cur.fetchall())
        self.assertTrue(NEW_INDEXES <= set(defs), sorted(defs))
        self.assertIn('gin_trgm_ops', defs['prop_city_trgm'])
        self.assertIn('upper', defs['prop_city_trgm'].lower())
        self.assertIn('gin_trgm_ops', defs['prop_location_trgm'])
        self.assertIn("to_tsvector('simple'", defs['prop_fts_gin'])
        self.assertIn('WHERE is_active', defs['prop_active_rank_idx'])

    def test_pg_trgm_installed(self):
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            self.assertIsNotNone(cur.fetchone())

    def test_indexes_are_valid(self):
        # A failed CREATE INDEX CONCURRENTLY leaves an INVALID index behind.
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                WHERE c.relname = ANY(%s) AND NOT i.indisvalid
                """,
                [list(NEW_INDEXES)],
            )
            self.assertEqual(cur.fetchall(), [])


class PlannerUsesIndexTest(TestCase):
    """Each assertion pins the exact SQL shape the application emits."""

    @classmethod
    def setUpTestData(cls):
        make_property(org=_prod_org(), title='Modern apartment', city='Lahore')

    def test_full_text_search_uses_gin(self):
        # No is_active filter here: on a near-empty table the planner would
        # otherwise prefer the partial rank index and hide an expression mismatch.
        qs = apply_text_search(Property.objects.all(), 'modern apart')
        self.assertIn('prop_search_tsv_gin', _plan(qs))      # stored column (T-1070f)

    def test_ref_no_search_uses_unique_index(self):
        qs = apply_text_search(Property.objects.all(), 'rt-lhr-2026-000000001')
        self.assertIn('properties_ref_no', _plan(qs))

    def test_city_icontains_uses_trigram(self):
        # PropertySearchService._from_db, recommendations, discovery engine.
        self.assertIn('prop_city_trgm', _plan(Property.objects.filter(city__icontains='lahor')))

    def test_city_iexact_uses_trigram(self):
        # PropertyViewSet ?city=, recommendations.similar_to.
        self.assertIn('prop_city_trgm', _plan(Property.objects.filter(city__iexact='Lahore')))

    def test_location_icontains_uses_trigram(self):
        self.assertIn(
            'prop_location_trgm', _plan(Property.objects.filter(location__icontains='dha phase')),
        )

    def test_ranked_consumer_search_uses_rank_index(self):
        qs = Property.objects.visible_to(None).order_by('-ai_score', '-created_at')[:10]
        self.assertIn('prop_active_rank_idx', _plan(qs))


class BuildPrefixTsqueryTest(TestCase):

    def test_tokens_are_prefix_and_anded(self):
        self.assertEqual(build_prefix_tsquery('Modern Apart'), 'modern:* & apart:*')

    def test_single_character_tokens_match_exactly(self):
        self.assertEqual(build_prefix_tsquery('5 marla'), '5 & marla:*')

    def test_tsquery_operators_and_quotes_are_stripped(self):
        self.assertEqual(
            build_prefix_tsquery("dha | !gulberg & (x:*) <-> 'y' \\ _"),
            'dha:* & gulberg:* & x & y',
        )

    def test_unicode_words_are_kept(self):
        self.assertEqual(build_prefix_tsquery('لاہور مکان'), 'لاہور:* & مکان:*')

    def test_empty_or_punctuation_only_is_none(self):
        for term in ('', '   ', '!!! & |', None, '___'):
            with self.subTest(term=term):
                self.assertIsNone(build_prefix_tsquery(term))

    def test_token_count_is_capped(self):
        raw = build_prefix_tsquery(' '.join(f'w{i}' for i in range(20)))
        self.assertEqual(raw.count('&'), MAX_TOKENS - 1)

    def test_input_length_is_capped(self):
        self.assertEqual(build_prefix_tsquery('a' * 500), 'a' * 200 + ':*')

    def test_every_generated_query_is_valid_tsquery(self):
        samples = ["' OR 1=1 --", 'a:b:c', '((dha))', 'x & & y', '!', 'RT-LHR-2026-000000001']
        with connection.cursor() as cur:
            for term in samples:
                raw = build_prefix_tsquery(term)
                if raw is None:
                    continue
                with self.subTest(term=term):
                    cur.execute("SELECT to_tsquery('simple', %s)::text", [raw])
                    cur.fetchone()


class ApiFullTextSearchTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        org = _prod_org()
        cls.apartment = make_property(
            org=org, title='Modern apartments with view', city='Karachi',
            location='Clifton Block 5', description='Sea facing',
        )
        cls.house = make_property(
            org=org, title='Corner house', city='Lahore', location='DHA Phase 6',
            description='Near the park; modern kitchen',
        )
        cls.plot = make_property(
            org=org, title='Residential plot', city='Islamabad', location='Bahria Town',
        )
        cls.sandbox = make_property(
            org=make_org(name='Sandbox Org'), title='Modern apartment sandbox',
        )
        cls.user = make_client()

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def _titles(self, **params):
        resp = self.api.get('/api/v1/properties/', params)
        self.assertEqual(resp.status_code, 200, resp.content)
        return [row['title'] for row in resp.json()['results']]

    def test_prefix_matches_plural(self):
        self.assertIn('Modern apartments with view', self._titles(search='apartment'))

    def test_all_terms_must_match(self):
        self.assertEqual(self._titles(search='modern clifton'), ['Modern apartments with view'])

    def test_matches_location_description_and_city(self):
        self.assertEqual(self._titles(search='bahria'), ['Residential plot'])
        self.assertEqual(self._titles(search='kitchen'), ['Corner house'])
        self.assertEqual(self._titles(search='islamabad'), ['Residential plot'])

    def test_ref_no_is_searchable(self):
        ref = Property.objects.get(pk=self.plot.pk).ref_no
        self.assertEqual(self._titles(search=ref), ['Residential plot'])
        self.assertEqual(self._titles(search=f'  {ref.lower()} '), ['Residential plot'])

    def test_partial_ref_prefix_uses_full_text(self):
        ref = Property.objects.get(pk=self.plot.pk).ref_no   # RT-ISL-<year>-<seq>
        self.assertIn('Residential plot', self._titles(search=ref.rsplit('-', 1)[0]))

    def test_title_and_description_matches_both_returned(self):
        # Fields are unweighted (one document); relevance ordering is ts_rank.
        titles = self._titles(search='modern')
        self.assertEqual(set(titles), {'Modern apartments with view', 'Corner house'})

    def test_sandbox_listing_stays_hidden_from_consumers(self):
        self.assertNotIn('Modern apartment sandbox', self._titles(search='modern apartment'))

    def test_combined_with_city_filter(self):
        self.assertEqual(self._titles(search='modern', city='lahore'), ['Corner house'])

    def test_junk_search_returns_empty_not_error(self):
        self.assertEqual(self._titles(search='&&& !!! ::'), [])

    def test_no_match(self):
        self.assertEqual(self._titles(search='penthouse'), [])


class MigrationShapeTest(TestCase):

    def test_indexes_built_concurrently_outside_a_transaction(self):
        from django.contrib.postgres.operations import AddIndexConcurrently

        mod = importlib.import_module('apps.properties.migrations.0022_search_indexes')
        self.assertFalse(mod.Migration.atomic)
        names = {
            op.index.name for op in mod.Migration.operations
            if isinstance(op, AddIndexConcurrently)
        }
        self.assertEqual(names, NEW_INDEXES - {'prop_search_tsv_gin'})
        mod = importlib.import_module('apps.properties.migrations.0037_search_tsv_index')
        self.assertFalse(mod.Migration.atomic)
        self.assertEqual([op.index.name for op in mod.Migration.operations
                          if isinstance(op, AddIndexConcurrently)], ['prop_search_tsv_gin'])
