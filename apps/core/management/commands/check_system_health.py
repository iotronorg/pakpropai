"""
Management command: check_system_health

Audits every critical subsystem and prints a colour-coded status report.
Exits 0 only when all checks pass; exits 1 on any FAIL or WARN.

Usage:
    python manage.py check_system_health
    python manage.py check_system_health --json      # machine-readable output
    python manage.py check_system_health --warn-ok   # exit 0 even on WARN
"""
import json
import sys
import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Audits database, Redis, Celery workers, migrations, and app config"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--warn-ok",
            action="store_true",
            help="Exit 0 even when WARN checks are present",
        )

    def handle(self, *args, **options):
        report = {
            "database":        self._check_database(),
            "redis":           self._check_redis(),
            "celery_workers":  self._check_celery_workers(),
            "migrations":      self._check_migrations(),
            "whatsapp_config": self._check_whatsapp_config(),
            "ai_config":       self._check_ai_config(),
        }

        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self._print_report(report)

        has_fail = any(v["status"] == "fail" for v in report.values())
        has_warn = any(v["status"] == "warn" for v in report.values())

        if has_fail:
            sys.exit(1)
        if has_warn and not options["warn_ok"]:
            sys.exit(1)
        sys.exit(0)

    # ── individual checks ─────────────────────────────────────────────────────

    def _check_database(self) -> dict:
        from django.db import connection
        try:
            t0 = time.monotonic()
            with connection.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]
            ms = round((time.monotonic() - t0) * 1000)
            return {"status": "ok", "latency_ms": ms, "version": version.split(",")[0]}
        except Exception as exc:
            return {"status": "fail", "error": str(exc)}

    def _check_redis(self) -> dict:
        from django.core.cache import cache
        probe_key = "_mgmt_health_probe"
        probe_val = "ok"
        try:
            t0 = time.monotonic()
            cache.set(probe_key, probe_val, 30)
            result = cache.get(probe_key)
            ms = round((time.monotonic() - t0) * 1000)
            if result != probe_val:
                return {"status": "fail", "error": "read-back mismatch — cache may be corrupted"}
            return {"status": "ok", "latency_ms": ms}
        except Exception as exc:
            return {"status": "fail", "error": str(exc)}

    def _check_celery_workers(self) -> dict:
        try:
            from config.celery import app as celery_app
            inspector = celery_app.control.inspect(timeout=3.0)
            active = inspector.active()
            if not active:
                return {
                    "status": "warn",
                    "note": "No workers responded within 3s — workers may be offline or slow to start",
                }
            stats = inspector.stats() or {}
            worker_info = {}
            for name, tasks in active.items():
                pool = (stats.get(name) or {}).get("pool", {})
                worker_info[name] = {
                    "active_tasks": len(tasks),
                    "processes": pool.get("processes", []),
                }
            return {"status": "ok", "workers": worker_info}
        except Exception as exc:
            return {"status": "fail", "error": str(exc)}

    def _check_migrations(self) -> dict:
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        try:
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if plan:
                pending = [f"{app}.{name}" for (app, name), _ in plan]
                return {
                    "status": "warn",
                    "pending_count": len(pending),
                    "pending": pending,
                }
            return {"status": "ok", "pending_count": 0}
        except Exception as exc:
            return {"status": "fail", "error": str(exc)}

    def _check_whatsapp_config(self) -> dict:
        from django.conf import settings
        missing = [
            k for k in ("WA_ACCESS_TOKEN", "WA_APP_SECRET", "WA_PHONE_NUMBER_ID", "WA_VERIFY_TOKEN")
            if not getattr(settings, k, "")
        ]
        if missing:
            return {
                "status": "warn",
                "note": f"WhatsApp sending/receiving disabled — missing: {', '.join(missing)}",
            }
        return {"status": "ok", "note": "All WhatsApp credentials present"}

    def _check_ai_config(self) -> dict:
        from django.conf import settings
        backend = getattr(settings, "AI_BACKEND", "gemini")
        if backend == "gemini":
            key = getattr(settings, "GEMINI_API_KEY", "")
            if not key:
                return {"status": "warn", "backend": "gemini", "note": "GEMINI_API_KEY not set"}
            return {"status": "ok", "backend": "gemini", "model": getattr(settings, "GEMINI_MODEL", "")}
        else:
            ollama_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
            try:
                import urllib.request
                t0 = time.monotonic()
                urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=2)
                ms = round((time.monotonic() - t0) * 1000)
                return {"status": "ok", "backend": "ollama", "url": ollama_url, "latency_ms": ms}
            except Exception as exc:
                return {"status": "fail", "backend": "ollama", "url": ollama_url, "error": str(exc)}

    # ── report printer ────────────────────────────────────────────────────────

    def _print_report(self, report: dict) -> None:
        sep = "─" * 62
        self.stdout.write(f"\n{sep}")
        self.stdout.write("  RealTron AI — System Health Report")
        self.stdout.write(sep)

        for check, data in report.items():
            status = data["status"]
            if status == "ok":
                icon = self.style.SUCCESS("[ OK ]")
            elif status == "warn":
                icon = self.style.WARNING("[WARN]")
            else:
                icon = self.style.ERROR("[FAIL]")

            label = check.upper().ljust(18)
            detail = self._format_detail(data)
            self.stdout.write(f"  {icon}  {label}  {detail}")

        self.stdout.write(f"{sep}\n")

    @staticmethod
    def _format_detail(data: dict) -> str:
        status = data["status"]
        if status == "ok":
            if "latency_ms" in data:
                extra = f"  version={data['version']}" if "version" in data else ""
                return f"{data['latency_ms']}ms{extra}"
            if "workers" in data:
                names = ", ".join(data["workers"].keys())
                return f"{len(data['workers'])} worker(s): {names}"
            if "pending_count" in data:
                return "all migrations applied"
            return data.get("note", "")
        if status == "warn":
            if "pending" in data:
                pending = data["pending"]
                sample = ", ".join(pending[:3])
                suffix = f" (+{len(pending) - 3} more)" if len(pending) > 3 else ""
                return f"{len(pending)} pending: {sample}{suffix}"
            return data.get("note", "degraded")
        return data.get("error", "unknown error")
