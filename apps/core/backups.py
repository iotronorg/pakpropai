"""
Backup and restore-drill status as Prometheus metrics (T-6010).

Read at scrape time from ``BackupRun`` (one query per kind, cached per process):

* ``realtron_backup_last_success_timestamp_seconds{kind}`` — 0 when there has never
  been one, so "no backup yet" alerts like a stale one.
* ``realtron_backup_last_failure_timestamp_seconds{kind}`` — 0 when none.
* ``realtron_backup_last_size_bytes`` — the latest successful backup.

A database error yields no samples; the alert rules treat ``absent()`` as missing.
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)


class BackupStatusCollector:
    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._at = 0.0
        self._state: dict | None = None

    def describe(self):
        return []

    def _read(self) -> dict:
        from apps.core.models import BackupRun
        state = {'size': None}
        for kind in BackupRun.Kind.values:
            for status in BackupRun.Status.values:
                last = (BackupRun.objects.filter(kind=kind, status=status)
                        .order_by('-finished_at').values('finished_at', 'size_bytes').first())
                state[(kind, status)] = last['finished_at'].timestamp() if last else 0.0
                if last and kind == BackupRun.Kind.BACKUP and status == BackupRun.Status.OK:
                    state['size'] = last['size_bytes']
        return state

    def collect(self):
        from prometheus_client.core import GaugeMetricFamily
        from apps.core.models import BackupRun
        with self._lock:
            if self._state is None or time.monotonic() - self._at > self.ttl:
                try:
                    self._state = self._read()
                    self._at = time.monotonic()
                except Exception:
                    logger.warning('backups.status_read_failed', exc_info=True)
                    self._state, self._at = None, 0.0
            state = self._state
        if state is None:
            return
        ok = GaugeMetricFamily('realtron_backup_last_success_timestamp_seconds',
                               'Unix time of the last successful backup or restore drill (0 = never)',
                               labels=['kind'])
        failed = GaugeMetricFamily('realtron_backup_last_failure_timestamp_seconds',
                                   'Unix time of the last failed backup or restore drill (0 = never)',
                                   labels=['kind'])
        for kind in BackupRun.Kind.values:
            ok.add_metric([kind], state[(kind, BackupRun.Status.OK)])
            failed.add_metric([kind], state[(kind, BackupRun.Status.FAILED)])
        yield ok
        yield failed
        if state['size'] is not None:
            size = GaugeMetricFamily('realtron_backup_last_size_bytes',
                                     'Size of the last successful backup file (bytes)')
            size.add_metric([], state['size'])
            yield size


backup_status = BackupStatusCollector()
