"""
T-1100 / ADR-015: listing search indexes.

Built with CREATE INDEX CONCURRENTLY, so writes to ``properties`` are not
blocked; that requires running outside a transaction (``atomic = False``).
If a build is interrupted Postgres leaves an INVALID index: drop it
(``DROP INDEX CONCURRENTLY <name>``) and re-run ``migrate``.
"""
import django.contrib.postgres.indexes
import django.contrib.postgres.search
import django.db.models.functions.text
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('properties', '0021_pg_trgm_extension'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='property',
            index=django.contrib.postgres.indexes.GinIndex(
                django.contrib.postgres.search.SearchVector(
                    'title', 'city', 'location', 'description', 'ref_no', config='simple',
                ),
                name='prop_fts_gin',
            ),
        ),
        AddIndexConcurrently(
            model_name='property',
            index=django.contrib.postgres.indexes.GinIndex(
                django.contrib.postgres.indexes.OpClass(
                    django.db.models.functions.text.Upper('city'), name='gin_trgm_ops',
                ),
                name='prop_city_trgm',
            ),
        ),
        AddIndexConcurrently(
            model_name='property',
            index=django.contrib.postgres.indexes.GinIndex(
                django.contrib.postgres.indexes.OpClass(
                    django.db.models.functions.text.Upper('location'), name='gin_trgm_ops',
                ),
                name='prop_location_trgm',
            ),
        ),
        AddIndexConcurrently(
            model_name='property',
            index=models.Index(
                fields=['-ai_score', '-created_at'],
                condition=models.Q(is_active=True),
                name='prop_active_rank_idx',
            ),
        ),
    ]
