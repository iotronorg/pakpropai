"""
T-1070g-2 — STAGED (see README.md). Drops the legacy full-text expression
index; search uses prop_search_tsv_gin since T-1070f. CONCURRENTLY, so
atomic=False and no table lock. Reverse rebuilds it concurrently.
"""
from django.db import migrations

FTS_EXPR = ("to_tsvector('simple'::regconfig, "
            "COALESCE(title, '') || ' ' || COALESCE(city, '') || ' ' || COALESCE(location, '') "
            "|| ' ' || COALESCE(description, '') || ' ' || COALESCE(ref_no, ''))")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0042_drop_listing_v1_columns'),
    ]

    operations = [
        migrations.RunSQL(
            'DROP INDEX CONCURRENTLY IF EXISTS prop_fts_gin;',
            f'CREATE INDEX CONCURRENTLY IF NOT EXISTS prop_fts_gin ON properties USING gin ({FTS_EXPR});',
        ),
    ]
