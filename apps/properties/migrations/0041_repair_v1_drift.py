"""
T-1070g-1: repair drift left from before the 0040 trigger (pre-v2 writers →
v1 wins, as sync_listing_size / sync_listing_taxonomy did). Non-atomic,
batched (short row locks). After this, the trigger keeps drift at zero; the
g-2 gate (check_listing_contract) verifies it.
"""
from django.db import migrations

from apps.properties.listing_contract import repair_drift


def forwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        repair_drift(cur)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('properties', '0040_listing_v1_state_removal'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
