"""
T-1060b: public point columns (geo_lat/geo_lng) + the trigger that fills them
from latitude/longitude (snapped for private/approximate listings). Additive,
nullable, no rewrite. Backfill is 0043, the btree index 0044 (concurrent).
"""
from django.db import migrations, models

from apps.geo.sql import CREATE_TRIGGER_SQL, DROP_TRIGGER_SQL


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0041_repair_v1_drift'),
    ]

    operations = [
        migrations.AddField(
            model_name='property', name='geo_lat',
            field=models.DecimalField(blank=True, decimal_places=6, editable=False, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='property', name='geo_lng',
            field=models.DecimalField(blank=True, decimal_places=6, editable=False, max_digits=9, null=True),
        ),
        migrations.RunSQL(CREATE_TRIGGER_SQL, DROP_TRIGGER_SQL),
    ]
