"""
Root pytest configuration (covers tests/ and apps/*/tests.py).

The test settings use the real Redis cache, which Django does not reset between
tests. SystemConfigService caches config values there, so a flag enabled in one
test would otherwise leak into every later test after the DB rollback.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_cache():
    from django.core.cache import cache
    cache.clear()
    yield
