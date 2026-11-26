"""
T-1070g-2 (ADR-023 contract, release 2 of 2) — STAGED.

Move this file and 0053 into apps/properties/migrations/ only in the release
AFTER the one that shipped 0040/0041 (T-1070g-1) — see README.md here.

1. Gate: refuses (raises) unless ``check_listing_contract`` passes: g-1
   applied, mirror trigger present, zero drift, no unknown categories.
2. Drops the mirror trigger and the v1 columns area_marla, area_unit,
   area_sqm, property_type (their btree index goes with the column).
   DROP COLUMN is a catalog change (no rewrite); lock_timeout keeps it from
   queueing behind long transactions — on timeout, re-run.

Reverse (emergency rollback to a g-1 build): re-adds the columns, refills them
from size_*/category in one UPDATE (full-table; run off-peak), restores the
index and the trigger.
"""
from django.db import migrations

from apps.properties.listing_contract import CREATE_TRIGGER_SQL, DROP_TRIGGER_SQL, problems


def gate(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        found = problems(cur)
    if found:
        raise RuntimeError('T-1070g-2 refused; run `manage.py check_listing_contract`:\n- '
                           + '\n- '.join(found))


DROP_COLUMNS_SQL = """
SET LOCAL lock_timeout = '5s';
ALTER TABLE properties
    DROP COLUMN IF EXISTS area_marla,
    DROP COLUMN IF EXISTS area_unit,
    DROP COLUMN IF EXISTS area_sqm,
    DROP COLUMN IF EXISTS property_type;
"""

RESTORE_COLUMNS_SQL = """
ALTER TABLE properties
    ADD COLUMN IF NOT EXISTS area_marla    numeric(10, 2) NULL,
    ADD COLUMN IF NOT EXISTS area_unit     varchar(10)    NULL,
    ADD COLUMN IF NOT EXISTS area_sqm      numeric(12, 4) NULL,
    ADD COLUMN IF NOT EXISTS property_type varchar(30)    NULL;
UPDATE properties SET
    area_marla    = size_value,
    area_unit     = COALESCE(size_unit, 'marla'),
    area_sqm      = size_m2,
    property_type = CASE COALESCE(category, 'residential')
                        WHEN 'land' THEN 'plot' WHEN 'hospitality' THEN 'commercial'
                        ELSE COALESCE(category, 'residential') END;
ALTER TABLE properties
    ALTER COLUMN area_unit SET NOT NULL,
    ALTER COLUMN property_type SET NOT NULL;
CREATE INDEX IF NOT EXISTS properties_propert_f38ecc_idx ON properties (property_type);
"""


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0051_duplicate_clusters'),
    ]

    operations = [
        migrations.RunPython(gate, migrations.RunPython.noop),
        migrations.RunSQL(DROP_TRIGGER_SQL, CREATE_TRIGGER_SQL),
        migrations.RunSQL(DROP_COLUMNS_SQL, RESTORE_COLUMNS_SQL),
    ]
