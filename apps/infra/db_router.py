"""
OrgRegionalRouter — Django multi-database router for GDPR/UK GDPR data residency.

Reads the current request's org from thread-local context (set by
TenantIsolationMiddleware) and routes reads/writes to the correct regional cluster:

  org.data_residency_region = 'eu'  → 'eu_cluster'  (eu-west-1, Ireland)
  org.data_residency_region = 'uk'  → 'uk_cluster'  (eu-west-2, London)
  org.data_residency_region = 'uae' → 'uae_cluster' (me-south-1, Bahrain — optional)
  everything else                   → 'default'

Falls back to 'default' (returns None, which Django treats as "use default") when:
  - No org in thread-local context (unauthenticated, admin, background tasks)
  - The regional alias is not registered in DATABASES (cluster not yet provisioned)
  - Any exception — fail-open, never block a request

allow_migrate returns True for all databases so that migrations run on every cluster
(keeps schema in sync across all regions).
"""
import logging
import threading

logger = logging.getLogger(__name__)

_ctx = threading.local()


# ── Public API for middleware ─────────────────────────────────────────────────

def set_db_org(org) -> None:
    """Set the org whose regional cluster should be used for this thread's queries."""
    _ctx.org = org


def clear_db_org() -> None:
    """Clear the org context at the end of a request."""
    _ctx.org = None


# ── Router ────────────────────────────────────────────────────────────────────

class OrgRegionalRouter:
    """
    Django DB router that sends reads/writes for the current request's org
    to its designated regional cluster.
    """

    def _alias(self) -> str | None:
        """Return the DB alias for the current request's org, or None for default."""
        try:
            from django.conf import settings
            from apps.compliance.regional_router import RegionalDataRouter

            org = getattr(_ctx, 'org', None)
            if org is None:
                return None

            alias = RegionalDataRouter().get_db_alias(org)

            # 'default' means no regional cluster — use Django's default routing
            if alias == 'default':
                return None

            # Only route to the alias if it's actually registered in DATABASES.
            # If the cluster isn't provisioned yet, fall back to default silently.
            if alias not in settings.DATABASES:
                logger.debug(
                    'OrgRegionalRouter: alias %s not in DATABASES — falling back to default '
                    '(cluster not yet provisioned for org %s)',
                    alias, getattr(org, 'id', '?'),
                )
                return None

            return alias

        except Exception:
            logger.warning('OrgRegionalRouter._alias error — using default', exc_info=True)
            return None

    def db_for_read(self, model, **hints):
        return self._alias()

    def db_for_write(self, model, **hints):
        return self._alias()

    def allow_relation(self, obj1, obj2, **hints):
        db1 = getattr(obj1._state, 'db', 'default') or 'default'
        db2 = getattr(obj2._state, 'db', 'default') or 'default'
        # Allow within the same cluster and always allow to/from default
        if db1 == db2 or 'default' in (db1, db2):
            return True
        return None  # defer to next router

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # Run every migration on every registered database so all clusters stay in sync
        return True
