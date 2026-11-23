"""
T-1060c: public-point trigger v2 — listings without coordinates use their
location's geocoded centroid (snapped). Replaces the function from
properties 0042 (reverse restores v1). Then re-fires the trigger for listings
that have a location but no coordinates (batched, non-atomic; none qualify
until locations are geocoded).
"""
from django.db import migrations

from apps.geo.sql import CREATE_TRIGGER_SQL, CREATE_TRIGGER_SQL_V2

BATCH = 5000


def refire(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        last = None
        while True:
            cur.execute(
                "SELECT p.id FROM properties p JOIN locations l ON l.id = p.location_ref_id "
                "WHERE p.latitude IS NULL AND l.latitude IS NOT NULL "
                + ("AND p.id > %s " if last else "") + "ORDER BY p.id LIMIT %s",
                ([last, BATCH] if last else [BATCH]))
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                break
            cur.execute("UPDATE properties SET location_ref_id = location_ref_id WHERE id = ANY(%s)", [ids])
            last = ids[-1]


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0044_geo_point_index'),
        ('locations', '0003_location_geocode'),
    ]

    operations = [
        migrations.RunSQL(CREATE_TRIGGER_SQL_V2, CREATE_TRIGGER_SQL),
        migrations.RunPython(refire, migrations.RunPython.noop),
    ]
