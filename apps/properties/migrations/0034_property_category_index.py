"""
Index for the category filter (T-1070d). Built CONCURRENTLY so writes to
`properties` are not blocked; that requires ``atomic = False`` (as 0022/0026).
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0033_seed_amenities'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='property',
            index=models.Index(fields=['category', 'is_active'], name='prop_category_active_idx'),
        ),
    ]
