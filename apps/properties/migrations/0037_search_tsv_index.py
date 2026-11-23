"""
GIN index on the stored document (T-1070f). Built CONCURRENTLY (atomic=False),
as 0022. The legacy expression index prop_fts_gin stays until T-1070g.
"""
import django.contrib.postgres.indexes
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0036_backfill_search_tsv'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='property',
            index=django.contrib.postgres.indexes.GinIndex(fields=['search_tsv'],
                                                           name='prop_search_tsv_gin'),
        ),
    ]
