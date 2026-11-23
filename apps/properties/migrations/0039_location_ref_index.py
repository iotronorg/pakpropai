"""Index for location filters (T-1060a). Built CONCURRENTLY (atomic=False), as 0022."""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0038_location_ref_expand'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='property',
            index=models.Index(fields=['location_ref', 'is_active'], name='prop_location_ref_idx'),
        ),
    ]
