"""
Record one database backup or restore drill (T-6010).

Called by ``scripts/ops/backup.sh`` and ``scripts/ops/restore_drill.sh`` on the
host, through ``docker compose exec web``. Only metadata is stored: the file
name, size and checksum, never the file or its key.

    python manage.py record_backup_run --kind backup --status ok \\
        --started 2026-11-27T02:00:00Z --file realtron-20261127T020000Z.dump.gpg \\
        --size 123456 --sha256 <hex> --host db1 --detail "pg_restore --list ok"
"""
import re
from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import BackupRun

_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_FILE = re.compile(r'^[A-Za-z0-9._-]{1,200}$')
DETAIL_MAX = 2000


def _when(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        raise CommandError(f'Not an ISO 8601 time: {raw!r}') from None
    return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)


class Command(BaseCommand):
    help = 'Record a database backup or restore drill run (metadata only).'

    def add_arguments(self, parser):
        parser.add_argument('--kind', required=True, choices=BackupRun.Kind.values)
        parser.add_argument('--status', required=True, choices=BackupRun.Status.values)
        parser.add_argument('--started', required=True, help='ISO 8601, e.g. 2026-11-27T02:00:00Z')
        parser.add_argument('--finished', help='ISO 8601; default now')
        parser.add_argument('--file', default='', help='Backup file name (no directory)')
        parser.add_argument('--size', type=int, help='Bytes')
        parser.add_argument('--sha256', default='')
        parser.add_argument('--host', default='')
        parser.add_argument('--detail', default='')

    def handle(self, *args, **opts):
        file_name = opts['file']
        if file_name and not _FILE.match(file_name):
            raise CommandError('--file must be a bare file name (letters, digits, . _ -).')
        sha256 = opts['sha256'].lower()
        if sha256 and not _SHA256.match(sha256):
            raise CommandError('--sha256 must be 64 hex characters.')
        if opts['size'] is not None and opts['size'] < 0:
            raise CommandError('--size cannot be negative.')
        started = _when(opts['started'])
        finished = _when(opts['finished']) if opts['finished'] else timezone.now()
        if finished < started:
            raise CommandError('--finished is before --started.')
        run = BackupRun.objects.create(
            kind=opts['kind'], status=opts['status'], started_at=started, finished_at=finished,
            file_name=file_name, size_bytes=opts['size'], sha256=sha256, host=opts['host'][:100],
            detail=opts['detail'][:DETAIL_MAX])
        self.stdout.write(f'recorded {run.kind} {run.status} #{run.pk}')
