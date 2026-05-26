"""
Redis Cache Layer Crash Injector.

Simulates a total Redis node failure while concurrent organization operations
(inventory updates, deal-lock initiations, agent-session locks) are in-flight.

Modes
-----
patch   (default, no privileges required)
    Replaces the underlying redis.client.Redis command dispatcher with a proxy
    that raises redis.exceptions.ConnectionError on every call. Also wraps
    django.core.cache so all cache.get/set/delete/incr operations fail.

subprocess  (--redis-real flag)
    Sends SHUTDOWN NOSAVE to the running Redis process and restarts it via
    `redis-server <config>` or `systemctl restart redis`. Requires the
    calling process to have appropriate system permissions.

Expected system response under injection
-----------------------------------------
- EscrowDeal lock state must be read from the DB (not cache) → no double-locks.
- Inventory counters must come from Property.objects.count() → no balance drift.
- WhatsApp agent-session locks that live only in Redis will expire on recovery
  (this is acceptable per the orphan-revert Celery task).
- No duplicate entries in EscrowDeal, WhatsAppSession, or inventory rows.
- Cache warm-up on recovery must be idempotent.
"""
from __future__ import annotations

import contextlib
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterator
from unittest.mock import patch, MagicMock

log = logging.getLogger("chaos.redis_injector")


@dataclass
class RedisInjectionContext:
    mode: str = "patch"
    duration_s: float = 0.0
    ops_blocked: int = 0
    errors: list[str] = field(default_factory=list)


class _BlockingRedisProxy:
    """
    Drop-in replacement for a redis.client.Redis instance.
    Every method call raises ConnectionError and increments the ops_blocked counter.
    """

    def __init__(self, counter: list[int]):
        self._counter = counter  # mutable list so threads can share the counter

    def __getattr__(self, name: str) -> Callable:
        def _raiser(*args, **kwargs):  # noqa: ARG001
            self._counter[0] += 1
            raise ConnectionError(f"[CHAOS] Redis is DOWN — op={name} blocked")
        return _raiser

    def execute_command(self, *args, **kwargs):  # noqa: ARG002
        self._counter[0] += 1
        raise ConnectionError("[CHAOS] Redis is DOWN — execute_command blocked")

    # Django's cache backend checks `client.connection_pool.connection_kwargs`
    @property
    def connection_pool(self):
        mock = MagicMock()
        mock.connection_kwargs = {"host": "chaos-injected"}
        return mock


class RedisCrashInjector:
    def __init__(self, dry_run: bool = False, mode: str = "patch"):
        self.dry_run = dry_run
        self.mode    = mode

    # ── Patch-mode helpers ───────────────────────────────────────────────────

    def _get_django_cache_client(self):
        """Return the underlying redis.client.Redis from Django's cache backend."""
        from django.core.cache import cache
        backend = getattr(cache, "_cache", None)                    # django-redis
        if backend is None:
            backend = getattr(cache, "client", None)                # redis-py-cache
        client  = getattr(backend, "_client", None) or \
                  getattr(backend, "get_client", lambda: None)()
        return client

    @contextlib.contextmanager
    def _patch_mode(self, ctx: RedisInjectionContext) -> Iterator[None]:
        """
        Replace the redis client used by Django's cache framework AND the
        global redis module classes so any direct instantiation also fails.
        """
        import redis as redis_module
        from django.core.cache import cache, caches

        ops_counter = [0]
        proxy = _BlockingRedisProxy(ops_counter)

        # ── 1. Patch all named Django cache backends ──────────────────────
        original_clients: dict[str, object] = {}
        cache_names = getattr(caches, "_caches", {}).keys() or ["default"]

        for name in list(cache_names):
            try:
                c = caches[name]
                backend = getattr(c, "_cache", None) or getattr(c, "client", None)
                if backend is not None:
                    original_clients[name] = (backend, getattr(backend, "_client", None))
                    if hasattr(backend, "_client"):
                        backend._client = proxy
            except Exception as exc:
                log.debug("Could not patch cache backend %s: %s", name, exc)

        # ── 2. Patch direct redis.Redis / StrictRedis construction ────────
        with patch.object(redis_module, "Redis",        _BlockingRedisFactory(ops_counter)), \
             patch.object(redis_module, "StrictRedis",  _BlockingRedisFactory(ops_counter)):

            log.info("[CHAOS] Redis patch active — all Redis ops will raise ConnectionError")
            yield

        # ── 3. Restore Django cache backend clients ───────────────────────
        for name, (backend, orig_client) in original_clients.items():
            if hasattr(backend, "_client"):
                backend._client = orig_client

        ctx.ops_blocked = ops_counter[0]
        log.info("[CHAOS] Redis restored — %d ops were blocked during injection", ops_counter[0])

    # ── Subprocess-mode helpers ──────────────────────────────────────────────

    @contextlib.contextmanager
    def _subprocess_mode(self, ctx: RedisInjectionContext) -> Iterator[None]:
        """
        Stop the actual Redis process for maximum realism.
        Requires the caller to have SHUTDOWN + restart permissions.
        Only used when --redis-real is passed.
        """
        import subprocess
        from django.conf import settings

        redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        host, port = _parse_redis_addr(redis_url)

        log.warning("[CHAOS] Sending SHUTDOWN NOSAVE to Redis at %s:%s", host, port)
        try:
            subprocess.run(
                ["redis-cli", "-h", host, "-p", str(port), "SHUTDOWN", "NOSAVE"],
                timeout=5,
                check=False,
            )
        except FileNotFoundError:
            log.error("redis-cli not found — cannot use subprocess mode")
            ctx.errors.append("redis-cli not found")
            yield
            return

        time.sleep(0.5)
        log.info("[CHAOS] Redis is DOWN")
        yield

        log.info("[CHAOS] Restarting Redis…")
        restart_cmds = [
            ["systemctl", "restart", "redis"],
            ["service", "redis-server", "restart"],
            ["redis-server", "--daemonize", "yes"],
        ]
        restarted = False
        for cmd in restart_cmds:
            try:
                result = subprocess.run(cmd, timeout=10, check=False, capture_output=True)
                if result.returncode == 0:
                    log.info("[CHAOS] Redis restarted via: %s", " ".join(cmd))
                    restarted = True
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        if not restarted:
            log.error("[CHAOS] Could not auto-restart Redis — manual restart required")
            ctx.errors.append("auto-restart failed")

        time.sleep(1.0)

    # ── Public context manager ───────────────────────────────────────────────

    @contextlib.contextmanager
    def inject(self) -> Iterator[RedisInjectionContext]:
        ctx = RedisInjectionContext(mode=self.mode)

        if self.dry_run:
            log.info("[DRY RUN] Redis crash skipped — simulating 0-connection state")
            yield ctx
            return

        t0 = time.perf_counter()
        try:
            if self.mode == "subprocess":
                with self._subprocess_mode(ctx):
                    yield ctx
            else:
                with self._patch_mode(ctx):
                    yield ctx
        finally:
            ctx.duration_s = time.perf_counter() - t0

    # ── Post-recovery cache warm-up ──────────────────────────────────────────

    @staticmethod
    def warm_up_cache(org_ids: list[str]) -> dict[str, int]:
        """
        After Redis recovery, force-repopulate the most critical cache keys
        from their DB sources of truth. Returns a summary of keys warmed.
        """
        from django.core.cache import cache
        from apps.config.services import SystemConfigService
        from apps.organizations.models import Organization

        warmed: dict[str, int] = {"org_configs": 0, "feature_flags": 0, "system_config": 0}

        # System config
        try:
            SystemConfigService._refresh_cache()
            warmed["system_config"] = 1
        except Exception as exc:
            log.warning("system_config warm-up failed: %s", exc)

        # Org-level feature flags
        for org_id in org_ids:
            try:
                from apps.config.services import OrgConfigService
                OrgConfigService.reload(org_id)
                warmed["org_configs"] += 1
            except Exception:
                pass

        log.info("Cache warm-up complete: %s", warmed)
        return warmed


class _BlockingRedisFactory:
    """Callable that returns a _BlockingRedisProxy regardless of constructor args."""

    def __init__(self, counter: list[int]):
        self._counter = counter

    def __call__(self, *args, **kwargs):  # noqa: ARG002
        return _BlockingRedisProxy(self._counter)


def _parse_redis_addr(redis_url: str) -> tuple[str, int]:
    """Parse redis://host:port/db → (host, port)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(redis_url)
        return parsed.hostname or "localhost", parsed.port or 6379
    except Exception:
        return "localhost", 6379
