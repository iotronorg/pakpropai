"""Check the unified audit trail's hash chain (T-3070, ADR-060). Exit 1 when it is broken."""
import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Verify the audit_events hash chain from the first row to the last.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Print the result as JSON')

    def handle(self, *args, **o):
        from apps.core.audit import verify
        v = verify()
        if o['json']:
            self.stdout.write(json.dumps(v.as_dict()))
        if not v.ok:
            raise CommandError(f'Audit chain broken at #{v.broken_at}: {v.reason} ({v.checked} rows fine before it).')
        if not o['json']:
            self.stdout.write(f'OK: {v.checked} rows, head #{v.head_seq} {v.head_hash}')
