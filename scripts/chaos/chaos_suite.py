#!/usr/bin/env python3
"""
RealTron AI — Production Chaos Engineering Suite
=================================================
Principal SRE tool for automated fault injection, HA validation, and latency
profiling of the WhatsApp AI pipeline and REST API layer.

Scenarios
---------
  db-starvation   Exhaust PostgreSQL connection pool during live WA traffic.
                  Asserts: Redis buffering, Celery retry, zero session drops.

  redis-crash     Total Redis failure during concurrent org inventory ops.
                  Asserts: DB fallback, zero duplicate locks, zero balance drift.

Profiler
--------
  --profile-only  Run only the E2E latency tracer (no fault injection).
                  Outputs a Markdown table with P50/P95/P99 per pipeline stage.

Usage
-----
  # From pakpropai/ with venv active:
  python scripts/chaos/chaos_suite.py --scenario=all

  # Dry-run (simulates without touching real services):
  python scripts/chaos/chaos_suite.py --dry-run

  # Latency profile only:
  python scripts/chaos/chaos_suite.py --profile-only --samples=100

  # Real Redis shutdown (requires redis-cli access):
  python scripts/chaos/chaos_suite.py --scenario=redis-crash --redis-real
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Django bootstrap ──────────────────────────────────────────────────────────
# chaos_suite.py lives at pakpropai/scripts/chaos/ — pakpropai IS the Django root.
_HERE       = Path(__file__).resolve().parent
_PAKPROPAI  = _HERE.parents[1]   # scripts/chaos → scripts → pakpropai

sys.path.insert(0, str(_PAKPROPAI))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django
django.setup()

# ── Local imports (after Django setup) ───────────────────────────────────────
sys.path.insert(0, str(_HERE))

from fault_injectors.db_injector    import DBConnectionStarvationInjector
from fault_injectors.redis_injector import RedisCrashInjector
from load_generators.whatsapp_traffic import WhatsAppTrafficGenerator
from load_generators.api_traffic    import fire_inventory_ops
from profiler.latency_tracer        import LatencyTracer
from profiler.reporter              import ChaosReporter
from validators.state_validator     import StateValidator

_REPORT_DIR = _HERE / "reports"
_REPORT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            _REPORT_DIR / f"chaos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        ),
    ],
)
log = logging.getLogger("chaos.suite")


# ── Report data model ─────────────────────────────────────────────────────────

@dataclass
class Assertion:
    label: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    duration_s: float
    assertions: list[Assertion] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    error: Optional[str] = None

    def assert_that(self, label: str, condition: bool, detail: str = "") -> None:
        self.assertions.append(Assertion(label, condition, detail))
        if not condition:
            self.passed = False
            log.warning("ASSERTION FAILED: %s — %s", label, detail)
        else:
            log.info("✓ %s — %s", label, detail)


@dataclass
class SuiteReport:
    run_id: str
    started_at: str
    finished_at: str
    scenarios: list[ScenarioResult] = field(default_factory=list)
    latency_profile: dict = field(default_factory=dict)

    @property
    def all_passed(self) -> bool:
        return all(s.passed for s in self.scenarios)

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.scenarios)


# ── Orchestrator ──────────────────────────────────────────────────────────────

class ChaosOrchestrator:
    def __init__(
        self,
        dry_run: bool = False,
        concurrency: int = 20,
        redis_mode: str = "patch",
        base_url: str = "http://localhost:8000",
    ):
        self.dry_run     = dry_run
        self.concurrency = concurrency
        self.redis_mode  = redis_mode
        self.base_url    = base_url
        self.validator   = StateValidator()
        self.run_id      = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # ── Scenario 1: DB Connection Pool Starvation ─────────────────────────────

    def _run_db_starvation(self) -> ScenarioResult:
        result = ScenarioResult(name="db-connection-pool-starvation", passed=True, duration_s=0)
        t0     = time.perf_counter()
        log.info("══════ SCENARIO 1: DB Connection Pool Starvation ══════")

        injector  = DBConnectionStarvationInjector(dry_run=self.dry_run)
        generator = WhatsAppTrafficGenerator(
            concurrency=self.concurrency,
            dry_run=self.dry_run,
            base_url=self.base_url,
        )

        # Install Redis buffering middleware BEFORE injection so the task handler
        # can persist raw payloads when the DB is unavailable.
        if not self.dry_run:
            injector.install_redis_buffer_middleware()

        baseline = self.validator.snapshot_celery_task_state()
        log.info("Baseline — task_count=%d retried=%d",
                 baseline["task_count"], baseline["retried_count"])

        try:
            with injector.inject() as ctx:
                log.info(
                    "DB starved (%d/%d connections held) — firing %d concurrent WA messages",
                    ctx.connections_held, ctx.max_connections, self.concurrency,
                )

                traffic = generator.fire_conversation_burst(
                    count=self.concurrency, timeout_s=45
                )

                result.assert_that(
                    "WhatsApp webhook endpoint accepted all messages during DB starvation",
                    traffic.accepted == self.concurrency,
                    f"accepted={traffic.accepted}/{self.concurrency}  "
                    f"rejected={traffic.rejected}  errored={traffic.errored}",
                )

                redis_buffered = self.validator.count_redis_raw_payloads()
                result.assert_that(
                    "Raw message payloads buffered in Redis during DB outage",
                    redis_buffered > 0,
                    f"chaos:buffer:* keys found={redis_buffered}",
                )

                retry_delta = self.validator.count_celery_retries(
                    since=baseline["retried_count"]
                )
                result.assert_that(
                    "Celery task retry queue populated (exponential backoff active)",
                    retry_delta > 0,
                    f"new_retries_observed={retry_delta}",
                )

            # ── Post-recovery validation (DB connections released) ────────────
            log.info("DB connections released — waiting 5s for worker recovery…")
            time.sleep(5)

            sessions_ok = self.validator.verify_whatsapp_sessions_intact(
                expected_count=traffic.accepted
            )
            result.assert_that(
                "WhatsApp sessions structurally intact after DB recovery",
                sessions_ok,
                "sessions queryable from ORM post-recovery",
            )

            dropped = self.validator.count_dropped_messages()
            result.assert_that(
                "Zero inbound message drops (no failed WhatsAppMessage rows)",
                dropped == 0,
                f"failed_message_rows={dropped}",
            )

            result.metrics = {
                "connections_held": ctx.connections_held,
                "max_connections": ctx.max_connections,
                "injection_duration_s": round(ctx.duration_s, 2),
                "traffic_dispatched": traffic.dispatched,
                "traffic_accepted": traffic.accepted,
                "traffic_avg_latency_ms": round(traffic.avg_latency_ms, 1),
                "redis_buffered": redis_buffered,
                "retry_delta": retry_delta,
                "dropped_messages": dropped,
            }

        except Exception as exc:
            log.exception("DB starvation scenario encountered unexpected exception")
            result.passed = False
            result.error  = str(exc)

        result.duration_s = time.perf_counter() - t0
        log.info("DB starvation — %s (%.1fs)", "PASS" if result.passed else "FAIL", result.duration_s)
        return result

    # ── Scenario 2: Redis Cache Layer Crash ───────────────────────────────────

    def _run_redis_crash(self) -> ScenarioResult:
        result = ScenarioResult(name="redis-cache-layer-crash", passed=True, duration_s=0)
        t0     = time.perf_counter()
        log.info("══════ SCENARIO 2: Redis Cache Layer Crash ══════")

        injector = RedisCrashInjector(dry_run=self.dry_run, mode=self.redis_mode)
        org_ids  = self.validator.get_active_org_ids(limit=5)

        if not org_ids:
            log.warning("No active organizations — skipping Redis crash scenario")
            result.passed = False
            result.error  = "no_active_orgs"
            result.duration_s = time.perf_counter() - t0
            return result

        before_locks     = self.validator.snapshot_deal_locks(org_ids)
        before_inventory = self.validator.snapshot_inventory_counts(org_ids)
        log.info(
            "Pre-injection baseline — orgs=%d  locked_deals=%d  inventory_items=%d",
            len(org_ids), sum(before_locks.values()), sum(before_inventory.values()),
        )

        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            op_results: list[dict] = []

            with injector.inject() as ctx:
                log.info("[CHAOS] Redis crashed — firing %d concurrent inventory ops",
                         self.concurrency)

                with ThreadPoolExecutor(
                    max_workers=self.concurrency,
                    thread_name_prefix="chaos-redis",
                ) as pool:
                    futures = [
                        pool.submit(
                            fire_inventory_ops,
                            org_id,
                            base_url=self.base_url,
                            dry_run=self.dry_run,
                        )
                        for org_id in (org_ids * 4)[: self.concurrency]
                    ]
                    for future in as_completed(futures, timeout=60):
                        try:
                            op_results.append(future.result())
                        except Exception as exc:
                            op_results.append({"success": False, "error": str(exc)})

            result.assert_that(
                "Inventory GET ops complete without Redis (Django ORM DB fallback)",
                all(r.get("success") for r in op_results),
                f"ops={len(op_results)}  "
                f"failed={sum(1 for r in op_results if not r.get('success'))}",
            )

            log.info("[CHAOS] Redis restored — waiting 3s for cache warm-up…")
            time.sleep(3)

            if not self.dry_run:
                RedisCrashInjector.warm_up_cache(org_ids)

            after_locks     = self.validator.snapshot_deal_locks(org_ids)
            after_inventory = self.validator.snapshot_inventory_counts(org_ids)

            dupes = self.validator.detect_duplicate_lock_entries()
            result.assert_that(
                "No duplicate EscrowDeal lock rows after Redis recovery",
                dupes == 0,
                f"duplicate_locks={dupes}",
            )

            drift = self.validator.compute_balance_drift(before_inventory, after_inventory)
            result.assert_that(
                "Inventory item counts unchanged (zero balance drift)",
                drift == 0,
                f"total_drift={drift}  before={sum(before_inventory.values())}  "
                f"after={sum(after_inventory.values())}",
            )

            lock_ok = self.validator.verify_lock_consistency(before_locks, after_locks, op_results)
            result.assert_that(
                "Deal lock counts consistent (no unexpected unlocks)",
                lock_ok,
                f"before={sum(before_locks.values())}  after={sum(after_locks.values())}",
            )

            cache_ok = self.validator.verify_cache_matches_db(org_ids)
            result.assert_that(
                "Cache-vs-DB consistency restored after warm-up",
                all(cache_ok.values()),
                f"orgs_consistent={sum(cache_ok.values())}/{len(cache_ok)}",
            )

            result.metrics = {
                "orgs_tested": len(org_ids),
                "ops_fired": len(op_results),
                "ops_blocked_by_redis": ctx.ops_blocked,
                "injection_duration_s": round(ctx.duration_s, 2),
                "duplicate_locks": dupes,
                "balance_drift": drift,
                "lock_consistent": lock_ok,
            }

        except Exception as exc:
            log.exception("Redis crash scenario encountered unexpected exception")
            result.passed = False
            result.error  = str(exc)

        result.duration_s = time.perf_counter() - t0
        log.info("Redis crash — %s (%.1fs)", "PASS" if result.passed else "FAIL", result.duration_s)
        return result

    # ── Latency profiler ──────────────────────────────────────────────────────

    def _run_latency_profile(self, samples: int, concurrency: int) -> dict:
        log.info("══════ PROFILER: E2E Latency Tracing (%d samples) ══════", samples)
        tracer = LatencyTracer(dry_run=self.dry_run, base_url=self.base_url)
        return tracer.profile_pipeline(
            sample_count=samples,
            concurrency=concurrency,
            audio_pct=0.2,
        )

    # ── Suite runner ──────────────────────────────────────────────────────────

    def run(
        self,
        scenarios: list[str],
        profile: bool = True,
        profile_samples: int = 50,
    ) -> SuiteReport:
        report = SuiteReport(
            run_id=self.run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at="",
        )

        if "db-starvation" in scenarios or "all" in scenarios:
            report.scenarios.append(self._run_db_starvation())

        if "redis-crash" in scenarios or "all" in scenarios:
            report.scenarios.append(self._run_redis_crash())

        if profile:
            report.latency_profile = self._run_latency_profile(
                samples=profile_samples,
                concurrency=min(self.concurrency, 10),
            )

        report.finished_at = datetime.now(timezone.utc).isoformat()
        self._save_report(report)
        self._print_summary(report)
        return report

    # ── Output ────────────────────────────────────────────────────────────────

    def _save_report(self, report: SuiteReport) -> None:
        # Serialise dataclasses to plain dicts for JSON
        def _to_dict(obj):
            if hasattr(obj, "__dataclass_fields__"):
                return asdict(obj)
            return obj

        json_path = _REPORT_DIR / f"chaos_report_{self.run_id}.json"
        json_path.write_text(json.dumps(asdict(report), indent=2, default=str))

        md_path = _REPORT_DIR / f"chaos_report_{self.run_id}.md"
        md_path.write_text(ChaosReporter.render_markdown(report))

        log.info("Reports saved:\n  JSON → %s\n  MD   → %s", json_path, md_path)

    def _print_summary(self, report: SuiteReport) -> None:
        w = 72
        print("\n" + "═" * w)
        print(f"  CHAOS SUITE  RUN {report.run_id}  ({report.total_duration_s:.1f}s total)")
        print("═" * w)
        for s in report.scenarios:
            icon = "✅ PASS" if s.passed else "❌ FAIL"
            print(f"\n  {icon}  {s.name} ({s.duration_s:.1f}s)")
            for a in s.assertions:
                bullet = "    ✓" if a.passed else "    ✗"
                print(f"{bullet}  {a.label}")
                if a.detail:
                    print(f"       {a.detail}")
            if s.error:
                print(f"\n  ⚠  Exception: {s.error}")

        if report.latency_profile:
            print("\n" + "─" * w)
            print(ChaosReporter.render_latency_table(report.latency_profile))

        overall = "✅  ALL SCENARIOS PASSED" if report.all_passed else "❌  FAILURES DETECTED"
        print("\n" + "═" * w)
        print(f"  RESULT: {overall}")
        print("═" * w + "\n")


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="RealTron AI Chaos Engineering Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--scenario", default="all",
        choices=["all", "db-starvation", "redis-crash"],
        help="Which fault scenario to run (default: all)",
    )
    p.add_argument("--concurrency", type=int, default=20,
                   help="Number of concurrent WA messages / API ops (default: 20)")
    p.add_argument("--dry-run", action="store_true",
                   help="Simulate without touching real services")
    p.add_argument("--redis-real", action="store_true",
                   help="Use subprocess mode (SHUTDOWN NOSAVE) for Redis crash")
    p.add_argument("--profile-only", action="store_true",
                   help="Run only the E2E latency profiler, skip fault scenarios")
    p.add_argument("--samples", type=int, default=50,
                   help="Number of pipeline traces for the latency profiler (default: 50)")
    p.add_argument("--base-url", default="http://localhost:8000",
                   help="Base URL of the running Django app (default: http://localhost:8000)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    orchestrator = ChaosOrchestrator(
        dry_run     = args.dry_run,
        concurrency = args.concurrency,
        redis_mode  = "subprocess" if args.redis_real else "patch",
        base_url    = args.base_url,
    )

    if args.profile_only:
        profile = orchestrator._run_latency_profile(
            samples=args.samples,
            concurrency=min(args.concurrency, 10),
        )
        print(ChaosReporter.render_latency_table(profile))
        return 0

    report = orchestrator.run(
        scenarios=[args.scenario],
        profile=True,
        profile_samples=args.samples,
    )
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
