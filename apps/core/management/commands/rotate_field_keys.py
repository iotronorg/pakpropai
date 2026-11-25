"""
Re-encrypt every encrypted column under the first key of FIELD_ENCRYPTION_KEYS (runbook: data incident).

Steps: put the NEW key first and keep the old one(s) after it, deploy, run this command,
check that ``--dry-run`` reports 0 values under an old key, then remove the old key.
"""
from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.core.crypto import PREFIX, EncryptedJSONField, EncryptedTextField, configured_keys, rotate_str

BATCH = 500


class Command(BaseCommand):
    help = 'Re-encrypt encrypted columns under the first FIELD_ENCRYPTION_KEYS key (counts only, never values).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Count values still under an older key')

    def handle(self, *args, **o):
        keys = configured_keys()
        if not keys:
            raise CommandError('FIELD_ENCRYPTION_KEYS is not set.')
        if not getattr(settings, 'FIELD_HASH_KEY', ''):
            raise CommandError('Set FIELD_HASH_KEY to a fixed value first: without it the lookup hashes are derived '
                               'from the first encryption key and would all change (ADR-049).')
        from cryptography.fernet import Fernet, InvalidToken
        primary = Fernet(keys[0])

        def old(value) -> bool:
            if not (isinstance(value, str) and value.startswith(PREFIX)):
                return bool(value)              # plain text in an encrypted column: rewrite it too
            try:
                primary.decrypt(value[len(PREFIX):].encode())
                return False
            except InvalidToken:
                return True

        total = 0
        for table, pk, columns in self._targets():
            n = self._rewrite(table, pk, columns, old, o['dry_run'])
            if n:
                self.stdout.write(f'{table}: {n}')
            total += n
        verb = 'under an older key or in plain text' if o['dry_run'] else 're-encrypted'
        self.stdout.write(f'{total} values {verb}.')
        if o['dry_run'] and total:
            raise CommandError(f'{total} values still need rotating.')

    @staticmethod
    def _targets():
        seen = {}
        for model in apps.get_models():
            if not model._meta.managed or model._meta.proxy:
                continue
            cols = [f.column for f in model._meta.local_concrete_fields
                    if isinstance(f, (EncryptedTextField, EncryptedJSONField))]
            if cols:
                seen.setdefault(model._meta.db_table, (model._meta.pk.column, set()))[1].update(cols)
        return [(t, pk, sorted(c)) for t, (pk, c) in sorted(seen.items())]

    @staticmethod
    def _rewrite(table, pk, columns, old, dry_run) -> int:
        qn = connection.ops.quote_name
        cols = ', '.join(qn(c) for c in columns)
        changed, last = 0, None
        while True:
            with transaction.atomic(), connection.cursor() as cur:
                where = f'WHERE {qn(pk)} > %s ' if last is not None else ''
                # Identifiers come from model metadata, quoted; values are parameters.
                cur.execute(f'SELECT {qn(pk)}, {cols} FROM {qn(table)} {where}ORDER BY {qn(pk)} LIMIT {BATCH} '  # nosec B608
                            f'{"" if dry_run else "FOR UPDATE"}', [last] if last is not None else [])
                rows = cur.fetchall()
                for row in rows:
                    stale = [c for c, v in zip(columns, row[1:]) if old(v)]
                    if not stale:
                        continue
                    changed += len(stale)
                    if dry_run:
                        continue
                    values = dict(zip(columns, row[1:]))
                    sets = ', '.join(f'{qn(c)} = %s' for c in stale)
                    cur.execute(f'UPDATE {qn(table)} SET {sets} WHERE {qn(pk)} = %s',  # nosec B608
                                [*(rotate_str(values[c]) for c in stale), row[0]])
            if len(rows) < BATCH:
                return changed
            last = rows[-1][0]
