"""
Tests for GLOBAL-7: OrgRegionalRouter — Django multi-DB router for data residency.

All tests use @override_settings to register/unregister fake cluster aliases so
no real database connection is needed.

10 tests:
  1. EU org routes reads to eu_cluster
  2. EU org routes writes to eu_cluster
  3. UK org routes reads to uk_cluster
  4. UK org routes writes to uk_cluster
  5. PK org (no regional cluster) returns None (use default)
  6. No org context → None (unauthenticated / background task)
  7. EU org but eu_cluster not in DATABASES → None (cluster not yet provisioned)
  8. allow_relation: same cluster → True
  9. allow_relation: cross-cluster with default involved → True
  10. allow_migrate: returns True for all databases (all clusters stay in sync)
"""
from unittest.mock import MagicMock

from django.test import TestCase, override_settings

from apps.infra.db_router import OrgRegionalRouter, clear_db_org, set_db_org

_FAKE_DB = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}

_DATABASES_WITH_EU_UK = {
    'default':    _FAKE_DB,
    'eu_cluster': _FAKE_DB,
    'uk_cluster': _FAKE_DB,
}

_DATABASES_DEFAULT_ONLY = {
    'default': _FAKE_DB,
}


def _org(region: str) -> MagicMock:
    org = MagicMock()
    org.data_residency_region = region
    org.id = f'org-{region}'
    return org


class OrgRegionalRouterTests(TestCase):

    def setUp(self):
        self.router = OrgRegionalRouter()

    def tearDown(self):
        clear_db_org()

    # ── Routing with clusters registered ─────────────────────────────────────

    @override_settings(DATABASES=_DATABASES_WITH_EU_UK)
    def test_eu_org_routes_reads_to_eu_cluster(self):
        set_db_org(_org('eu'))
        self.assertEqual(self.router.db_for_read(MagicMock()), 'eu_cluster')

    @override_settings(DATABASES=_DATABASES_WITH_EU_UK)
    def test_eu_org_routes_writes_to_eu_cluster(self):
        set_db_org(_org('eu'))
        self.assertEqual(self.router.db_for_write(MagicMock()), 'eu_cluster')

    @override_settings(DATABASES=_DATABASES_WITH_EU_UK)
    def test_uk_org_routes_reads_to_uk_cluster(self):
        set_db_org(_org('uk'))
        self.assertEqual(self.router.db_for_read(MagicMock()), 'uk_cluster')

    @override_settings(DATABASES=_DATABASES_WITH_EU_UK)
    def test_uk_org_routes_writes_to_uk_cluster(self):
        set_db_org(_org('uk'))
        self.assertEqual(self.router.db_for_write(MagicMock()), 'uk_cluster')

    # ── Fallback to default ───────────────────────────────────────────────────

    @override_settings(DATABASES=_DATABASES_DEFAULT_ONLY)
    def test_pk_org_returns_none_uses_default(self):
        """PK org has no regional cluster — router returns None (Django uses default)."""
        set_db_org(_org('pk'))
        self.assertIsNone(self.router.db_for_read(MagicMock()))
        self.assertIsNone(self.router.db_for_write(MagicMock()))

    @override_settings(DATABASES=_DATABASES_DEFAULT_ONLY)
    def test_no_org_context_returns_none(self):
        """No org in context (unauthenticated, Celery task) → use default DB."""
        clear_db_org()
        self.assertIsNone(self.router.db_for_read(MagicMock()))
        self.assertIsNone(self.router.db_for_write(MagicMock()))

    @override_settings(DATABASES=_DATABASES_DEFAULT_ONLY)
    def test_eu_org_fallback_when_cluster_not_provisioned(self):
        """EU org but eu_cluster not registered → None (cluster not yet provisioned)."""
        set_db_org(_org('eu'))
        # eu_cluster is NOT in DATABASES — router falls back silently
        self.assertIsNone(self.router.db_for_read(MagicMock()))
        self.assertIsNone(self.router.db_for_write(MagicMock()))

    # ── allow_relation ────────────────────────────────────────────────────────

    @override_settings(DATABASES=_DATABASES_WITH_EU_UK)
    def test_allow_relation_same_cluster(self):
        """Objects in the same cluster can always relate."""
        obj1 = MagicMock(); obj1._state.db = 'eu_cluster'
        obj2 = MagicMock(); obj2._state.db = 'eu_cluster'
        self.assertTrue(self.router.allow_relation(obj1, obj2))

    @override_settings(DATABASES=_DATABASES_WITH_EU_UK)
    def test_allow_relation_cross_cluster_with_default(self):
        """Relations between a regional cluster and default are allowed."""
        obj1 = MagicMock(); obj1._state.db = 'eu_cluster'
        obj2 = MagicMock(); obj2._state.db = 'default'
        self.assertTrue(self.router.allow_relation(obj1, obj2))

    # ── allow_migrate ─────────────────────────────────────────────────────────

    @override_settings(DATABASES=_DATABASES_WITH_EU_UK)
    def test_allow_migrate_returns_true_for_all_databases(self):
        """Migrations run on every database so all clusters stay schema-synced."""
        for db_alias in ('default', 'eu_cluster', 'uk_cluster'):
            self.assertTrue(
                self.router.allow_migrate(db_alias, 'users'),
                f"allow_migrate should return True for {db_alias}",
            )
