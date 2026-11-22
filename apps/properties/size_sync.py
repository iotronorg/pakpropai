"""
Size v2 backfill / drift repair (T-1070b, ADR-023).

Copies the deprecated v1 size columns (area_marla, area_unit, area_sqm) into the
v2 columns (size_value, size_unit, size_m2) wherever they differ. Used by the
backfill migration, and by ``manage.py sync_listing_size`` after a rollout:
pre-v2 code still running during a deploy writes only v1, while v2 code always
writes both, so any disagreement means v1 is the newer value.
"""
from django.db import connection

_DRIFT = (
    "size_value IS DISTINCT FROM area_marla "
    "OR size_unit IS DISTINCT FROM area_unit "
    "OR size_m2 IS DISTINCT FROM area_sqm"
)


def count_drift(cursor=None) -> int:
    cur = cursor or connection.cursor()
    cur.execute(f"SELECT count(*) FROM properties WHERE {_DRIFT}")
    return cur.fetchone()[0]


def sync_v1_to_v2(cursor=None, batch_size: int = 5000) -> int:
    """Copy v1 → v2 in batches (short row locks). Returns rows updated."""
    cur = cursor or connection.cursor()
    total = 0
    while True:
        cur.execute(
            f"UPDATE properties SET size_value = area_marla, size_unit = area_unit, size_m2 = area_sqm "
            f"WHERE id IN (SELECT id FROM properties WHERE {_DRIFT} LIMIT %s)",
            [batch_size],
        )
        total += cur.rowcount
        if cur.rowcount < batch_size:
            return total
