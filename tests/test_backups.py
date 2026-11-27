"""T-6010 — database backups and restore drills: the record command, the metrics, and the
host scripts (scripts/ops/) run against stub ``docker`` and ``gpg`` commands.

The real tools were exercised end to end (pg_dump → gpg → pg_restore into a throwaway
postgres:15-alpine); see TESTING_STATUS. These tests pin the scripts' logic in CI.
"""
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock, skipUnless

import yaml
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.core.backups import BackupStatusCollector
from apps.core.models import BackupRun

ROOT = Path(__file__).resolve().parent.parent
OPS = ROOT / 'scripts' / 'ops'
SHA = 'a' * 64
BASH = shutil.which('bash')


def samples(collector):
    out = {}
    for family in collector.collect():
        for s in family.samples:
            out[(s.name, s.labels.get('kind'))] = s.value
    return out


class RecordCommandTest(TestCase):
    def test_records_metadata(self):
        out = StringIO()
        call_command('record_backup_run', kind='backup', status='ok', started='2026-11-27T02:00:00Z',
                     file='realtron-20261127T020000Z.dump.gpg', size=1234, sha256=SHA.upper(), host='db1',
                     detail='pg_restore --list ok', stdout=out)
        run = BackupRun.objects.get()
        self.assertEqual((run.kind, run.status, run.size_bytes, run.sha256, run.host),
                         ('backup', 'ok', 1234, SHA, 'db1'))
        self.assertEqual(run.started_at.isoformat(), '2026-11-27T02:00:00+00:00')
        self.assertIn('recorded backup ok', out.getvalue())

    def test_refuses_bad_input(self):
        base = dict(kind='drill', status='failed', started='2026-11-27T02:00:00Z')
        for bad in ({'file': '../etc/passwd'}, {'file': '/var/backups/x.gpg'}, {'sha256': 'xyz'},
                    {'size': -1}, {'started': 'yesterday'}, {'finished': '2026-11-27T01:00:00Z'}):
            with self.subTest(bad=bad), self.assertRaises(CommandError):
                call_command('record_backup_run', **{**base, **bad})
        self.assertFalse(BackupRun.objects.exists())

    def test_detail_is_capped(self):
        call_command('record_backup_run', kind='drill', status='ok', started='2026-11-27T02:00:00Z',
                     detail='x' * 5000)
        self.assertEqual(len(BackupRun.objects.get().detail), 2000)


class BackupMetricsTest(TestCase):
    def run_(self, kind, status, hours_ago, size=None):
        at = timezone.now() - timedelta(hours=hours_ago)
        return BackupRun.objects.create(kind=kind, status=status, started_at=at, finished_at=at, size_bytes=size)

    def test_never_is_zero_so_it_reads_as_stale(self):
        got = samples(BackupStatusCollector())
        for kind in ('backup', 'drill'):
            self.assertEqual(got[('realtron_backup_last_success_timestamp_seconds', kind)], 0)
            self.assertEqual(got[('realtron_backup_last_failure_timestamp_seconds', kind)], 0)
        self.assertNotIn(('realtron_backup_last_size_bytes', None), got)

    def test_latest_runs(self):
        self.run_('backup', 'ok', 30, size=10)
        newest = self.run_('backup', 'ok', 2, size=20)
        failed = self.run_('backup', 'failed', 1)
        self.run_('drill', 'ok', 200)
        got = samples(BackupStatusCollector())
        self.assertAlmostEqual(got[('realtron_backup_last_success_timestamp_seconds', 'backup')],
                               newest.finished_at.timestamp(), places=3)
        self.assertAlmostEqual(got[('realtron_backup_last_failure_timestamp_seconds', 'backup')],
                               failed.finished_at.timestamp(), places=3)
        self.assertEqual(got[('realtron_backup_last_size_bytes', None)], 20)
        self.assertGreater(got[('realtron_backup_last_success_timestamp_seconds', 'drill')], 0)

    def test_cached_and_silent_on_db_errors(self):
        c = BackupStatusCollector(ttl=60)
        samples(c)
        with self.assertNumQueries(0):
            samples(c)
        broken = BackupStatusCollector()
        with mock.patch.object(BackupStatusCollector, '_read', side_effect=RuntimeError('db down')):
            self.assertEqual(samples(broken), {})               # absent() → BackupStatusAbsent

    def test_exported_on_metrics(self):
        self.run_('backup', 'ok', 1, size=5)
        from apps.core import backups
        backups.backup_status._state = None
        body = self.client.get('/metrics/', REMOTE_ADDR='127.0.0.1').content.decode()
        self.assertRegex(body, r'realtron_backup_last_success_timestamp_seconds\{kind="backup"\} 1\.\d+e\+09')
        self.assertIn('realtron_backup_last_size_bytes 5.0', body)


# ── The host scripts, with stub docker and gpg ──────────────────────────────

FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >> "$LOG"
case "$*" in
  *" exec -T db sh -c "*pg_dump*) [ -z "$FAIL_DUMP" ] || exit 1; printf 'PGDMP fake archive' ;;
  *" exec -T db pg_restore --list"*) cat > /dev/null; [ -z "$FAIL_LIST" ] || exit 1 ;;
  *" exec -T web python manage.py record_backup_run "*) [ -z "$FAIL_RECORD" ] || exit 1 ;;
  *" ps -q db"*) echo cid123 ;;
  "inspect "*) echo postgres:15-alpine ;;
  "run -d "*) echo drillcid ;;
  "exec "*" pg_isready "*) exit 0 ;;
  "exec -i "*" pg_restore "*) cat > /dev/null; [ -z "$FAIL_RESTORE" ] || exit 1 ;;
  "exec "*" psql "*)
    case "$*" in
      *"to_regclass('public.$MISSING_TABLE')"*) echo f ;;
      *to_regclass*) echo t ;;
      *"max(applied)"*) echo "2026-11-27 02:00:00+00" ;;
      *"count(*) FROM django_migrations"*) echo 311 ;;
      *count*) echo 7 ;;
    esac ;;
esac
exit 0
"""

# Symmetric "encryption" that marks the bytes, so tests can see nothing left unencrypted.
FAKE_GPG = r"""#!/bin/sh
out=""; mode=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) out=$2; shift ;;
    --symmetric) mode=enc ;;
    --decrypt) mode=dec ;;
    --passphrase-file) [ "$(cat "$2")" = "right" ] || BAD=1; shift ;;
    -*) ;;
    *) in=$1 ;;
  esac
  shift
done
if [ "$mode" = enc ]; then { printf 'ENC:'; cat; } > "$out"; exit 0; fi
[ -z "$BAD" ] || { echo "gpg: decryption failed: Bad session key" >&2; exit 2; }
tail -c +5 "$in"
"""


@skipUnless(BASH, 'bash is needed for the ops scripts')
class OpsScriptsTest(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        deploy = self.tmp / 'srv'
        (deploy / 'ops').mkdir(parents=True)
        (deploy / 'docker').mkdir()
        (deploy / 'docker' / 'docker-compose.prod.yml').write_text('services: {}\n')
        (deploy / '.env').write_text('X=1\n')
        for name in ('common.sh', 'backup.sh', 'restore_drill.sh'):
            shutil.copy(OPS / name, deploy / 'ops' / name)
        bin_dir = self.tmp / 'bin'
        bin_dir.mkdir()
        for name, body in (('docker', FAKE_DOCKER), ('gpg', FAKE_GPG)):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.key = self.tmp / 'key'
        self.key.write_text('right')
        self.key.chmod(0o600)
        self.backups = self.tmp / 'backups'
        self.deploy, self.bin, self.log = deploy, bin_dir, self.tmp / 'docker.log'

    def run_script(self, name, *args, **env):
        full = {'PATH': f'{self.bin}:/usr/bin:/bin', 'LOG': str(self.log), 'BACKUP_DIR': str(self.backups),
                'BACKUP_PASSPHRASE_FILE': str(self.key), 'HOST_LABEL': 'test-host', **env}
        r = subprocess.run([BASH, str(self.deploy / 'ops' / name), *args], env=full, capture_output=True,
                           text=True, timeout=60)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return r, calls

    def records(self, calls):
        return [c for c in calls if 'record_backup_run' in c]

    def dumps(self):
        return sorted(self.backups.glob('realtron-*.dump.gpg')) if self.backups.exists() else []

    # backup.sh

    def test_backup_writes_an_encrypted_checked_file_and_records_it(self):
        r, calls = self.run_script('backup.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        [dump] = self.dumps()
        self.assertRegex(dump.name, r'^realtron-\d{8}T\d{6}Z\.dump\.gpg$')
        self.assertTrue(dump.read_bytes().startswith(b'ENC:'))
        self.assertEqual(stat.S_IMODE(dump.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.backups.stat().st_mode), 0o700)
        sha = hashlib.sha256(dump.read_bytes()).hexdigest()
        self.assertEqual((self.backups / f'{dump.name}.sha256').read_text(), f'{sha}  {dump.name}\n')
        self.assertFalse(list(self.backups.glob('*.partial')))
        self.assertTrue(any('pg_restore --list' in c for c in calls))          # checked before it counts
        [rec] = self.records(calls)
        self.assertIn('--kind backup --status ok', rec)
        self.assertIn(f'--file {dump.name}', rec)
        self.assertIn(f'--sha256 {sha}', rec)
        self.assertIn('NOT copied off this host', rec)
        self.assertTrue(all(c.startswith('compose -p realtron --env-file .env -f docker/docker-compose.prod.yml ')
                            for c in calls))

    def test_no_plaintext_dump_is_ever_written(self):
        self.run_script('backup.sh')
        found = [p for p in self.tmp.rglob('*') if p.is_file() and p.parent != self.bin
                 and b'PGDMP' in p.read_bytes() and not p.read_bytes().startswith(b'ENC:')]
        self.assertEqual(found, [])

    def test_upload_hook_and_retention(self):
        self.backups.mkdir(mode=0o700)
        old = self.backups / 'realtron-20200101T000000Z.dump.gpg'
        old.write_text('old')
        (self.backups / f'{old.name}.sha256').write_text('x')
        for p in (old, self.backups / f'{old.name}.sha256'):
            os.utime(p, (0, 0))
        offsite = self.tmp / 'offsite'
        offsite.mkdir()
        r, calls = self.run_script('backup.sh', BACKUP_KEEP_DAYS='14', BACKUP_UPLOAD_CMD=f'cp "$1" {offsite}/')
        self.assertEqual(r.returncode, 0, r.stderr)
        [dump] = self.dumps()
        self.assertNotEqual(dump, old)
        self.assertEqual(sorted(p.name for p in offsite.iterdir()), [dump.name, f'{dump.name}.sha256'])
        self.assertIn('; uploaded', self.records(calls)[0])

    def test_failures_leave_no_file_and_are_recorded(self):
        for env in ({'FAIL_DUMP': '1'}, {'FAIL_LIST': '1'}, {'BACKUP_UPLOAD_CMD': 'exit 3'}):
            with self.subTest(env=env):
                shutil.rmtree(self.backups, ignore_errors=True)
                self.log.unlink(missing_ok=True)
                r, calls = self.run_script('backup.sh', **env)
                self.assertEqual(r.returncode, 1)
                self.assertIn('FAILED', r.stdout + r.stderr)
                self.assertFalse(list(self.backups.glob('*.partial')))
                [rec] = self.records(calls)
                self.assertIn('--kind backup --status failed', rec)
                if 'BACKUP_UPLOAD_CMD' not in env:
                    self.assertEqual(self.dumps(), [])

    def test_a_backup_that_cannot_be_recorded_still_succeeds(self):
        r, _ = self.run_script('backup.sh', FAIL_RECORD='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('could not record', r.stderr)
        self.assertEqual(len(self.dumps()), 1)

    def test_refuses_without_a_private_passphrase_file(self):
        for env, message in (({'BACKUP_PASSPHRASE_FILE': ''}, 'not set'),
                             ({'BACKUP_PASSPHRASE_FILE': str(self.tmp / 'nope')}, 'does not exist'),
                             ({}, 'mode 600 or 400'),
                             ({'BACKUP_KEEP_DAYS': '0'}, 'BACKUP_KEEP_DAYS')):
            with self.subTest(message=message):
                self.key.chmod(0o644 if message == 'mode 600 or 400' else 0o600)
                r, calls = self.run_script('backup.sh', **env)
                self.assertEqual(r.returncode, 1)
                self.assertIn(message, r.stderr)
        calls = self.log.read_text() if self.log.exists() else ''
        self.assertNotIn('pg_dump', calls)                     # refused before touching the database

    # restore_drill.sh

    def backup(self):
        r, _ = self.run_script('backup.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.log.unlink()
        return self.dumps()[-1]

    def test_drill_restores_the_newest_backup_into_a_throwaway_container(self):
        dump = self.backup()
        older = self.backups / 'realtron-20200101T000000Z.dump.gpg'
        shutil.copy(dump, older)
        os.utime(older)                                       # newest by mtime, oldest by name
        r, calls = self.run_script('restore_drill.sh')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'ok: {dump.name}', r.stdout)
        run = next(c for c in calls if c.startswith('run -d '))
        self.assertIn('--network none', run)
        self.assertIn('postgres:15-alpine', run)              # the live db's image
        self.assertTrue(any(c.startswith('exec -i ') and '--exit-on-error' in c for c in calls))
        self.assertTrue(any(c.startswith('rm -f realtron-restore-drill-') for c in calls))   # cleaned up
        [rec] = self.records(calls)
        self.assertIn('--kind drill --status ok', rec)
        self.assertIn('migrations=311', rec)
        self.assertIn('users=7', rec)
        self.assertFalse(any(' exec -T db ' in c and 'pg_restore' in c and '--list' not in c for c in calls),
                         'the live database must never be restored into')

    def test_drill_failures_are_recorded_and_cleaned_up(self):
        dump = self.backup()
        cases = {
            'checksum': lambda: (self.backups / f'{dump.name}.sha256').write_text(f'{"0" * 64}  {dump.name}\n'),
            'wrong key': lambda: self.key.write_text('wrong'),
            'restore': lambda: None,
            'missing table': lambda: None,
        }
        env = {'restore': {'FAIL_RESTORE': '1'}, 'missing table': {'MISSING_TABLE': 'users'}}
        for case, prepare in cases.items():
            with self.subTest(case=case):
                sha_file = self.backups / f'{dump.name}.sha256'
                sha_file.write_text(f'{hashlib.sha256(dump.read_bytes()).hexdigest()}  {dump.name}\n')
                self.key.write_text('right')
                self.log.unlink(missing_ok=True)
                prepare()
                r, calls = self.run_script('restore_drill.sh', str(dump), **env.get(case, {}))
                self.assertEqual(r.returncode, 1, case)
                [rec] = self.records(calls)
                self.assertIn('--kind drill --status failed', rec)
                if case != 'checksum':
                    self.assertTrue(any(c.startswith('rm -f realtron-restore-drill-') for c in calls))

    def test_drill_without_backups(self):
        r, calls = self.run_script('restore_drill.sh')
        self.assertEqual(r.returncode, 1)
        self.assertIn('no backups', r.stderr)

    # shipping and alerting

    def test_scripts_are_strict_and_shipped_by_the_deploy(self):
        for name in ('backup.sh', 'restore_drill.sh'):
            text = (OPS / name).read_text()
            self.assertTrue(text.startswith('#!/usr/bin/env bash\n'))
            self.assertIn('set -Eeuo pipefail\numask 077\n', text)
            self.assertTrue(os.access(OPS / name, os.X_OK), name)
        backup = (OPS / 'backup.sh').read_text()
        self.assertIn('--symmetric --cipher-algo AES256', backup)
        self.assertNotRegex(backup, r'pg_dump[^|\n]*>\s*"?\$')     # never dumped straight to a file
        steps = yaml.safe_load((ROOT / '.github/workflows/deploy.yml').read_text())
        text = yaml.safe_dump(steps)
        for name in ('common.sh', 'backup.sh', 'restore_drill.sh'):
            self.assertIn(f'scripts/ops/{name}', text)
        units = {p.name for p in (ROOT / 'ops' / 'systemd').glob('*')}
        self.assertEqual(units, {'realtron-backup.service', 'realtron-backup.timer',
                                 'realtron-restore-drill.service', 'realtron-restore-drill.timer'})

    def test_alerts_exist_for_missing_failed_and_overdue(self):
        rules = (ROOT / 'ops' / 'prometheus-alerts.yaml').read_text()
        for alert in ('BackupMissing', 'BackupFailed', 'RestoreDrillOverdue', 'BackupStatusAbsent'):
            self.assertIn(f'- alert: {alert}', rules)
            self.assertIn(f'docs/runbooks/backups.md#{alert.lower()}', rules)
        runbook = (ROOT / 'docs' / 'runbooks' / 'backups.md').read_text().lower()
        for alert in ('backupmissing', 'backupfailed', 'restoredrilloverdue', 'backupstatusabsent'):
            self.assertRegex(runbook, rf'(?m)^#+ {alert}$')
