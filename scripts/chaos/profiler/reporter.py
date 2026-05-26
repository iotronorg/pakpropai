"""
Chaos Engineering Report Generator.

Renders the SuiteReport and LatencyProfile into a formatted Markdown document
suitable for embedding in Confluence, Notion, GitHub PRs, or incident reports.

Outputs:
  - Full Markdown report (chaos_report_<run_id>.md)
  - A standalone latency table (printed to stdout by --profile-only)
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_STAGE_LABELS = {
    "webhook_landing":     "Webhook Landing",
    "audio_transcription": "Audio Transcription (STT)",
    "llm_orchestration":   "LLM Orchestration",
    "guardrail_check":     "Guardrail Compliance",
    "outbound_delivery":   "Outbound Meta Delivery",
}

_SLO_THRESHOLDS_MS = {
    "webhook_landing":     {"p95": 50,    "p99": 100},
    "audio_transcription": {"p95": 3000,  "p99": 5000},
    "llm_orchestration":   {"p95": 5000,  "p99": 8000},
    "guardrail_check":     {"p95": 300,   "p99": 500},
    "outbound_delivery":   {"p95": 800,   "p99": 1200},
}


def _slo_badge(stage: str, metric: str, value_ms: float) -> str:
    threshold = _SLO_THRESHOLDS_MS.get(stage, {}).get(metric)
    if threshold is None:
        return ""
    return " ✅" if value_ms <= threshold else " ❌ SLO breach"


def _fmt_ms(val: float) -> str:
    if val >= 1000:
        return f"{val / 1000:.2f}s"
    return f"{val:.0f}ms"


class ChaosReporter:

    @staticmethod
    def render_latency_table(profile: dict) -> str:
        """
        Render a Markdown table of P50 / P95 / P99 latencies per pipeline stage.
        This is the primary deliverable for latency profiling.
        """
        stage_metrics = profile.get("stage_metrics", {})
        lines = [
            "## RealTron AI — End-to-End Pipeline Latency Profile\n",
            f"**Samples:** {profile.get('sample_count', 0)}  ",
            f"**Error rate:** {profile.get('error_rate', 0) * 100:.1f}%  ",
            f"**Pipeline P50:** {_fmt_ms(profile.get('pipeline_p50_ms', 0))}  ",
            f"**Pipeline P95:** {_fmt_ms(profile.get('pipeline_p95_ms', 0))}  ",
            f"**Pipeline P99:** {_fmt_ms(profile.get('pipeline_p99_ms', 0))}\n",
            "| Stage | P50 | P95 | P99 | Mean | Min | Max | SLO P95 | SLO P99 |",
            "|-------|-----|-----|-----|------|-----|-----|---------|---------|",
        ]

        stage_order = [
            "webhook_landing",
            "audio_transcription",
            "llm_orchestration",
            "guardrail_check",
            "outbound_delivery",
        ]

        for stage in stage_order:
            if stage not in stage_metrics:
                continue
            m   = stage_metrics[stage]
            lbl = _STAGE_LABELS.get(stage, stage)
            slo_p95 = _SLO_THRESHOLDS_MS.get(stage, {}).get("p95")
            slo_p99 = _SLO_THRESHOLDS_MS.get(stage, {}).get("p99")

            slo_p95_badge = ("✅" if m["p95_ms"] <= slo_p95 else "❌") if slo_p95 else "—"
            slo_p99_badge = ("✅" if m["p99_ms"] <= slo_p99 else "❌") if slo_p99 else "—"

            lines.append(
                f"| {lbl} "
                f"| {_fmt_ms(m['p50_ms'])} "
                f"| {_fmt_ms(m['p95_ms'])} "
                f"| {_fmt_ms(m['p99_ms'])} "
                f"| {_fmt_ms(m['mean_ms'])} "
                f"| {_fmt_ms(m['min_ms'])} "
                f"| {_fmt_ms(m['max_ms'])} "
                f"| {slo_p95_badge} ({_fmt_ms(slo_p95)} limit) "
                f"| {slo_p99_badge} ({_fmt_ms(slo_p99)} limit) |"
            )

        lines.append("\n> SLO targets: Webhook ≤50ms P95, Guardrail ≤300ms P95, "
                     "LLM ≤5s P95, STT ≤3s P95, Delivery ≤800ms P95")
        return "\n".join(lines)

    @staticmethod
    def render_scenario_table(scenarios: list) -> str:
        lines = [
            "## Chaos Scenario Results\n",
            "| Scenario | Result | Duration | Assertions |",
            "|----------|--------|----------|------------|",
        ]
        for s in scenarios:
            status   = "✅ PASS" if s.get("passed") else "❌ FAIL"
            duration = f"{s.get('duration_s', 0):.1f}s"
            total    = len(s.get("assertions", []))
            passed   = sum(1 for a in s.get("assertions", []) if a.get("passed"))
            lines.append(f"| {s.get('name')} | {status} | {duration} | {passed}/{total} |")
        return "\n".join(lines)

    @staticmethod
    def render_assertion_detail(scenario: dict) -> str:
        lines = [f"### {scenario.get('name', 'Unknown')} — Assertion Detail\n"]
        for a in scenario.get("assertions", []):
            icon   = "✅" if a.get("passed") else "❌"
            detail = f" — `{a['detail']}`" if a.get("detail") else ""
            lines.append(f"- {icon} **{a['label']}**{detail}")
        if scenario.get("metrics"):
            lines.append("\n**Metrics captured:**")
            lines.append("```json")
            import json
            lines.append(json.dumps(scenario["metrics"], indent=2))
            lines.append("```")
        if scenario.get("error"):
            lines.append(f"\n> ⚠️ Exception: `{scenario['error']}`")
        return "\n".join(lines)

    @classmethod
    def render_markdown(cls, report) -> str:
        """Render the full SuiteReport as a Markdown document."""
        import json

        # Handle both dataclass and dict forms
        if hasattr(report, "__dict__"):
            import dataclasses
            data = dataclasses.asdict(report)
        else:
            data = report

        run_id   = data.get("run_id", "unknown")
        started  = data.get("started_at", "")
        finished = data.get("finished_at", "")
        scenarios = data.get("scenarios", [])
        profile   = data.get("latency_profile", {})
        all_pass  = all(s.get("passed") for s in scenarios)
        overall   = "✅ ALL SCENARIOS PASSED" if all_pass else "❌ FAILURES DETECTED"

        sections = [
            f"# RealTron AI — Chaos Engineering Report\n",
            f"**Run ID:** `{run_id}`  ",
            f"**Started:** {started}  ",
            f"**Finished:** {finished}  ",
            f"**Overall:** {overall}\n",
            "---\n",
        ]

        if scenarios:
            sections.append(cls.render_scenario_table(scenarios))
            sections.append("\n---\n")
            for s in scenarios:
                sections.append(cls.render_assertion_detail(s))
                sections.append("")

        if profile:
            sections.append("\n---\n")
            sections.append(cls.render_latency_table(profile))

        sections.append("\n---\n")
        sections.append("## Recommendations\n")
        sections.extend(cls._generate_recommendations(scenarios, profile))

        return "\n".join(sections)

    @staticmethod
    def _generate_recommendations(scenarios: list, profile: dict) -> list[str]:
        recs: list[str] = []

        # Check for DB starvation failures
        db_scenario = next((s for s in scenarios if "db" in s.get("name", "")), None)
        if db_scenario and not db_scenario.get("passed"):
            for a in db_scenario.get("assertions", []):
                if not a.get("passed") and "Redis" in a.get("label", ""):
                    recs.append(
                        "- 🔴 **Add Redis buffering to `process_incoming_whatsapp_task`**: "
                        "Catch `django.db.OperationalError` and push raw payload to "
                        "`chaos:buffer:<msg_id>` in Redis. Add a Celery beat task to drain "
                        "the buffer on DB recovery."
                    )
                if not a.get("passed") and "retry" in a.get("label", "").lower():
                    recs.append(
                        "- 🔴 **Enable Celery autoretry on `process_incoming_whatsapp_task`**: "
                        "Add `autoretry_for=(OperationalError,)`, `max_retries=5`, "
                        "`retry_backoff=True`, `retry_backoff_max=120` to the task decorator."
                    )

        # Check for Redis crash failures
        redis_scenario = next((s for s in scenarios if "redis" in s.get("name", "")), None)
        if redis_scenario and not redis_scenario.get("passed"):
            recs.append(
                "- 🟠 **Harden ORM querysets as cache fallback**: "
                "Wrap `cache.get()` calls in `try/except ConnectionError` and fall through "
                "to the equivalent DB query. Critical paths: `OrgConfigService.get()`, "
                "`UsageLedger.within_limit()`, agent-room lock check."
            )

        # Latency SLO breaches
        stage_metrics = profile.get("stage_metrics", {})
        if stage_metrics.get("llm_orchestration", {}).get("p99_ms", 0) > 8000:
            recs.append(
                "- 🟡 **LLM P99 exceeds 8s SLO**: Consider streaming responses via "
                "`agent.stream_chat()` and sending a WhatsApp typing indicator while "
                "the LLM generates. Alternatively, add a 6s timeout with graceful retry."
            )
        if stage_metrics.get("guardrail_check", {}).get("p95_ms", 0) > 300:
            recs.append(
                "- 🟢 **Guardrail P95 approaching SLO**: Ensure the Redis bloom-filter "
                "cache key is set correctly. Check `GuardrailEngine._cache_key()` and "
                "verify Redis TTL is 300s."
            )

        if not recs:
            recs.append("- ✅ No critical recommendations — system behaved as expected under all fault scenarios.")

        return recs
