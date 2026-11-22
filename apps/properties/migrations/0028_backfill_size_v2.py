"""
Backfill size_value/size_unit/size_m2 from area_marla/area_unit/area_sqm
(T-1070b). Batched UPDATEs outside one big transaction so row locks stay short.
SQL is inlined (not imported from apps.properties.size_sync) so this migration
stays frozen if that module changes. Reverse is a no-op: v1 columns are untouched.
"""
from django.db import migrations

_DRIFT = (
    "size_value IS DISTINCT FROM area_marla "
    "OR size_unit IS DISTINCT FROM area_unit "
    "OR size_m2 IS DISTINCT FROM area_sqm"
)


def backfill(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        while True:
            cur.execute(
                "UPDATE properties SET size_value = area_marla, size_unit = area_unit, "
                f"size_m2 = area_sqm WHERE id IN (SELECT id FROM properties WHERE {_DRIFT} LIMIT 5000)"
            )
            if cur.rowcount < 5000:
                break


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0027_size_v2_expand'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
