"""
Backfill category from the deprecated property_type (T-1070d): plot → land,
others unchanged. Batched UPDATEs outside one big transaction so row locks stay
short. SQL is inlined (not imported from apps.properties.taxonomy) so this
migration stays frozen if that module changes. Reverse is a no-op.
"""
from django.db import migrations

_DRIFT = ("category IS NULL OR CASE category WHEN 'land' THEN 'plot' "
          "WHEN 'hospitality' THEN 'commercial' ELSE category END IS DISTINCT FROM property_type")


def backfill(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        while True:
            cur.execute(
                "UPDATE properties SET category = CASE property_type WHEN 'plot' THEN 'land' "
                "ELSE property_type END, subtype = NULL "
                f"WHERE id IN (SELECT id FROM properties WHERE {_DRIFT} LIMIT 5000)"
            )
            if cur.rowcount < 5000:
                break


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0031_taxonomy_amenities_expand'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
