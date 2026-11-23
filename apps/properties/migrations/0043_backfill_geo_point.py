"""T-1060b: fill geo_lat/geo_lng for existing rows (batched; re-fires the 0042 trigger)."""
from django.db import migrations

BATCH = 5000


def forwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        while True:
            cur.execute(
                "UPDATE properties SET latitude = latitude WHERE id IN ("
                " SELECT id FROM properties WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
                " AND geo_lat IS NULL LIMIT %s)", [BATCH])
            if cur.rowcount < BATCH:
                break


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0042_geo_point_expand'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
