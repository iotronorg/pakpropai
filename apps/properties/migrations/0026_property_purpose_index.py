"""
Index for the purpose filter (T-1070a). Built CONCURRENTLY so writes to
`properties` are not blocked; that requires ``atomic = False`` (as 0022).
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0025_listing_schema_v2_expand'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='property',
            index=models.Index(fields=['purpose', 'is_active'], name='prop_purpose_active_idx'),
        ),
    ]
