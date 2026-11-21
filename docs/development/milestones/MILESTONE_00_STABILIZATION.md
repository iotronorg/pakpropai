# M0 — Stabilisation (PRD Phase 0)

**Goal:** make the existing system safe, measurable and buildable. No new product features.

## Exit criteria and state
| Criterion | State |
|---|---|
| CI green on `main` | All jobs fixed; suite 1452/1/0, Bandit 0 HIGH locally. **Pending:** first GitHub Actions run (needs a push) |
| Zero plaintext secrets | Done in code (ADR‑008); takes effect when migrations run with a key |
| Scrapers off | Done (default off) — operators must check DB rows |
| Sandbox exclusion tests passing | Done (21 tests) |
| Real token accounting | Done (with the AFC residual, KI‑006) |
| Frozen modules off | Done (deal lock, voice calls, syndication); verify DB rows per GO_LIVE_CHECKLIST |

## Tasks
T‑0001 … T‑0017 — see [DEVELOPMENT_TASKS](../DEVELOPMENT_TASKS.md#m0--stabilisation-phase-0).

## Remaining order
All tasks complete. M0 closes after the first green GitHub Actions run.
