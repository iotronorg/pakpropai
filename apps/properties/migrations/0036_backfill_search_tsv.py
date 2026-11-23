"""
Backfill search_tsv in batches (T-1070f). Touching search_tsv fires the
trigger from 0035, which computes the document, so the expression lives in one
place. Non-atomic: short row locks per batch. Reverse is a no-op.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        while True:
            cur.execute(
                "UPDATE properties SET search_tsv = NULL WHERE id IN "
                "(SELECT id FROM properties WHERE search_tsv IS NULL LIMIT 5000)"
            )
            if cur.rowcount < 5000:
                break


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0035_search_tsv_expand'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
