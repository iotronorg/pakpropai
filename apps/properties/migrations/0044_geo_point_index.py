"""T-1060b: btree on the public point (bounding-box prefilter). CONCURRENTLY, atomic=False."""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0043_backfill_geo_point'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='property',
            index=models.Index(fields=['geo_lat', 'geo_lng'], name='prop_geo_point_idx'),
        ),
    ]
