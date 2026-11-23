"""
T-1080 backfill (non-atomic, batches of 5000): inactive listings → 'archived';
active ones keep 'published' with published_at = created_at. No expiry is set
for existing listings (expires_at stays NULL). Rows not yet backfilled are the
ones with status_changed_at IS NULL (the trigger stamps every new row). Then
validates the status CHECK added NOT VALID in 0045.
"""
from django.db import migrations

from apps.properties.lifecycle_sql import CONSTRAINT

BATCH = 5000


def forwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        while True:
            cur.execute(
                "UPDATE properties SET "
                " status = CASE WHEN is_active THEN status ELSE 'archived' END, "
                " published_at = CASE WHEN is_active THEN COALESCE(published_at, created_at) "
                "                     ELSE published_at END, "
                " status_changed_at = COALESCE(updated_at, created_at, now()) "
                "WHERE id IN (SELECT id FROM properties WHERE status_changed_at IS NULL LIMIT %s)",
                [BATCH])
            if cur.rowcount < BATCH:
                break
        cur.execute(f"ALTER TABLE properties VALIDATE CONSTRAINT {CONSTRAINT}")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [('properties', '0045_listing_status_expand')]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
