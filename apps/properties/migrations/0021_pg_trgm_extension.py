"""
T-1100 / ADR-015: enable pg_trgm for the trigram indexes in 0022.

pg_trgm is a contrib extension shipped with every Postgres package and is
"trusted" since Postgres 13, so the database owner can create it without
superuser rights. ``CREATE EXTENSION IF NOT EXISTS`` is a no-op when an
operator has already installed it. The reverse keeps the extension: other
objects may depend on it.
"""
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class KeepTrigramExtension(TrigramExtension):
    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0020_backfill_is_sandbox'),
    ]

    operations = [
        KeepTrigramExtension(),
    ]
