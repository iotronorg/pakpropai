"""MVP acceptance 10: search every text column for national IDs and secrets that are not encrypted."""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models

from apps.core.crypto import PREFIX, EncryptedJSONField, EncryptedTextField

# POSIX regular expressions (PostgreSQL). Dashed IDs only: 13 bare digits are also phone numbers and timestamps.
PATTERNS = {
    'cnic': r'(^|[^0-9])[0-9]{5}-[0-9]{7}-[0-9]([^0-9]|$)',
    'emirates_id': r'(^|[^0-9])784-[0-9]{4}-[0-9]{7}-[0-9]([^0-9]|$)',
    'private_key': r'BEGIN [A-Z ]*PRIVATE KEY',
    'stripe_key': r'(sk|rk)_(live|test)_[A-Za-z0-9]{10}|whsec_[A-Za-z0-9]{10}',
    'google_key': r'AIza[A-Za-z0-9_-]{30}',
    'meta_token': r'EAA[A-Za-z0-9]{40}',
    'twilio_key': r'(^|[^A-Za-z0-9])SK[0-9a-f]{32}',
    'jwt': r'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.',
}


class Command(BaseCommand):
    help = 'Report national IDs and secrets stored in plain text (counts only). Exit code 1 when something is found.'

    def handle(self, *args, **options):
        found = 0
        with connection.cursor() as cur:
            for model in apps.get_models(include_auto_created=True):
                if not model._meta.managed or model._meta.proxy:
                    continue
                for field in model._meta.local_concrete_fields:
                    for what, n in self._check(cur, model._meta.db_table, field):
                        found += n
                        self.stdout.write(f'{model._meta.label}.{field.name}: {what}: {n}')
        if found:
            raise CommandError(f'{found} values in plain text.')
        self.stdout.write('No plain-text secrets or national IDs found.')

    def _check(self, cur, table, field):
        qn = connection.ops.quote_name
        col = f'{qn(field.column)}::text'
        if isinstance(field, (EncryptedTextField, EncryptedJSONField)):
            cur.execute(f"SELECT count(*) FROM {qn(table)} WHERE {col} <> '' AND {col} NOT LIKE %s",  # nosec B608
                        [PREFIX + '%'])
            if n := cur.fetchone()[0]:
                yield 'not encrypted', n
        elif isinstance(field, (models.CharField, models.TextField, models.JSONField)):
            for name, pattern in PATTERNS.items():
                cur.execute(f'SELECT count(*) FROM {qn(table)} WHERE {col} ~ %s', [pattern])  # nosec B608
                if n := cur.fetchone()[0]:
                    yield name, n
