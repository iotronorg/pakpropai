"""
PostgreSQL Connection Pool Starvation Injector.

Strategy: Open (max_connections - HEADROOM) raw psycopg2 connections, each
holding an idle BEGIN transaction, starving Django's ORM and Celery workers.
Django raises django.db.OperationalError on new query attempts.

Expected system response under injection:
  - WhatsApp Celery tasks should catch OperationalError and push raw payload
    into Redis under a chaos:buffer:<msg_id> key for deferred reprocessing.
  - Retries with exponential backoff should appear in the Celery queue.
  - WhatsAppSession rows must NOT be mutated mid-failure (no partial writes).
  - On restore, workers drain the Redis buffer and recover all sessions intact.

The injector exposes a single context manager: `inject()`.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator

log = logging.getLogger("chaos.db_injector")

# Leave this many free connections so monitoring queries + admin can still run.
_HEADROOM = 4
# Stagger connection open by this many ms to avoid SYN-flood on pg_hba.conf.
_OPEN_STAGGER_MS = 40


@dataclass
class DBInjectionContext:
    """Live state accessible to the caller inside the `with inject()` block."""
    connections_held: int = 0
    max_connections: int = 0
    duration_s: float = 0.0
    active_on_entry: int = 0
    errors: list[str] = field(default_factory=list)


class DBConnectionStarvationInjector:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    # ── DSN helpers ─────────────────────────────────────────────────────────

    def _get_dsn(self) -> str:
        from django.conf import settings
        db = settings.DATABASES["default"]
        host = db.get("HOST", "localhost") or "localhost"
        port = db.get("PORT", 5432) or 5432
        name = db["NAME"]
        user = db["USER"]
        pwd  = db.get("PASSWORD", "")
        return f"host={host} port={port} dbname={name} user={user} password={pwd} connect_timeout=5"

    def _query_pg(self, sql: str) -> list[tuple]:
        import psycopg2
        conn = psycopg2.connect(self._get_dsn())
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()
        finally:
            conn.close()

    def get_max_connections(self) -> int:
        try:
            rows = self._query_pg("SHOW max_connections;")
            return int(rows[0][0])
        except Exception as exc:
            log.warning("Could not read max_connections: %s — defaulting to 100", exc)
            return 100

    def count_active_connections(self) -> int:
        try:
            rows = self._query_pg(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();"
            )
            return int(rows[0][0])
        except Exception:
            return 0

    # ── Vampire thread ───────────────────────────────────────────────────────

    def _hold_connection(self, dsn: str, idx: int) -> None:
        """Hold one idle-in-transaction connection until stop_event fires."""
        import psycopg2

        try:
            conn = psycopg2.connect(dsn)
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute("BEGIN;")
                log.debug("Vampire #%d connected", idx)
                while not self._stop_event.wait(timeout=0.3):
                    try:
                        cur.execute("SELECT 1;")   # keepalive — prevents idle timeout
                    except Exception:
                        break
            conn.rollback()
            conn.close()
        except Exception as exc:
            log.debug("Vampire #%d failed to open/maintain: %s", idx, exc)

    def _exhaust_pool(self, target: int) -> None:
        dsn = self._get_dsn()
        self._threads = []
        for i in range(target):
            t = threading.Thread(
                target=self._hold_connection,
                args=(dsn, i),
                daemon=True,
                name=f"chaos-vampire-db-{i:03d}",
            )
            t.start()
            self._threads.append(t)
            time.sleep(_OPEN_STAGGER_MS / 1000)

        log.info("Opened %d vampire DB connections (staggered over %.1fs)",
                 target, target * _OPEN_STAGGER_MS / 1000)

    def _release_pool(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=5.0)
        alive = sum(1 for t in self._threads if t.is_alive())
        if alive:
            log.warning("%d vampire threads still alive after release", alive)
        else:
            log.info("All vampire DB connections released")
        self._threads.clear()

    # ── Public context manager ───────────────────────────────────────────────

    @contextlib.contextmanager
    def inject(self) -> Iterator[DBInjectionContext]:
        ctx = DBInjectionContext()

        if self.dry_run:
            log.info("[DRY RUN] DB starvation skipped — simulating 10 held connections")
            ctx.connections_held = 10
            ctx.max_connections = 100
            yield ctx
            return

        t0 = time.perf_counter()
        max_conn = self.get_max_connections()
        active   = self.count_active_connections()
        target   = max(1, max_conn - _HEADROOM - active)

        ctx.max_connections   = max_conn
        ctx.active_on_entry   = active
        ctx.connections_held  = target

        log.info(
            "PG max_connections=%d  active=%d  will open %d vampires  headroom=%d",
            max_conn, active, target, _HEADROOM,
        )

        self._stop_event.clear()
        self._exhaust_pool(target)

        # Confirm pool is actually exhausted before yielding.
        time.sleep(0.5)
        post_active = self.count_active_connections()
        log.info("Post-injection active connections: %d / %d", post_active, max_conn)

        try:
            yield ctx
        finally:
            log.info("Releasing all vampire connections…")
            self._release_pool()
            ctx.duration_s = time.perf_counter() - t0
            log.info("DB injection ended — held for %.2fs", ctx.duration_s)

    # ── Retry-buffer helper (installed as Django middleware during chaos) ────

    @staticmethod
    def install_redis_buffer_middleware(app_module: str = "apps.whatsapp.tasks") -> None:
        """
        Monkey-patches process_incoming_whatsapp_task to catch OperationalError
        and buffer the raw payload in Redis under chaos:buffer:<msg_id>.
        Simulates what a production-grade retry layer should do.
        """
        import importlib
        from django.core.cache import cache
        from django.db import OperationalError

        mod = importlib.import_module(app_module)
        original_task_fn = mod.process_incoming_whatsapp_task.__wrapped__

        def _buffered(message: dict, phone_number_id: str = "") -> None:
            try:
                original_task_fn(message, phone_number_id)
            except OperationalError as exc:
                msg_id    = message.get("id", "unknown")
                cache_key = f"chaos:buffer:{msg_id}"
                cache.set(cache_key, {"message": message, "pnid": phone_number_id}, timeout=3600)
                log.warning("DB unavailable — buffered msg_id=%s in Redis key %s", msg_id, cache_key)
                raise  # Let Celery retry machinery take over

        mod.process_incoming_whatsapp_task.__wrapped__ = _buffered
        log.info("Redis buffer middleware installed on process_incoming_whatsapp_task")
