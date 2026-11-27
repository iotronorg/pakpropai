# M6 — Production readiness (cross-phase)

**Goal:** the platform can be run, recovered and trusted in production: backups that are proven to restore, measured performance against the NFR targets, a security review with no open high/critical findings, and the operational checks of the launch checklist. Sources: [MVP_SCOPE §6 criteria 10–15, §7 launch checklist](../../product/ai-real-estate-agent/MVP_SCOPE.md), [TECHNICAL_RECOMMENDATIONS §16–19 (TR‑16…19), 14.10](../../product/ai-real-estate-agent/TECHNICAL_RECOMMENDATIONS.md), [PRD_MASTER](../../product/ai-real-estate-agent/PRD_MASTER.md) D19, NFR‑PERF‑001…003, NFR‑AVL‑001, NFR‑SEC‑001.

**Started:** 2026‑11‑27 (session 35, continued). M0–M4 are code complete apart from owner-gated and release-gated work; T‑1091 (API schema) finished the API debt.

**Deployment model it targets:** staging and production are each one Docker host (ADR‑053); Postgres runs as the `db` container on a local volume (`docker/docker-compose.prod.yml`). A managed database would change T‑6010.

## Exit criteria
| # | Criterion | Source | State |
|---|---|---|---|
| 1 | Encrypted database backups run on a schedule, leave the host, and a restore drill has passed and is recorded | MVP §7 "Backup restore drill completed"; TR‑17; D19 | Tooling done (T‑6010, ADR‑071); owner: off-host storage, timers installed and first drill on each server (checklist §71) |
| 2 | Load test against the NFR targets (REST p95 ≤ 300 ms, search p95 ≤ 400 ms at 100k listings) with the results recorded and regressions fixed | NFR‑PERF‑001/002; TR‑19 "Load" | T‑6020 |
| 3 | Security review pass: static scans clean or triaged, permissions matrix checked, data scan finds no plaintext secrets or national IDs | MVP §6 criterion 10; TR‑19 "Security" | T‑6030 |
| 4 | External pentest of the public API and chat with no open high/critical findings | MVP §6 criterion 12; TR 14.10 | Owner (vendor); T‑6030 prepares |
| 5 | Release flag audit: frozen modules off in production, verified by a command | MVP §7 "Feature flags verified" | Done (T‑6040, ADR‑072): `check_release`, run by every deploy; owner: run it on each server before the first deploy (checklist §72) |
| 6 | Documented RPO/RTO and capacity notes (PgBouncer, worker pools) | TR‑16, TR‑17 | T‑6050; targets are the owner's |

## Tasks
| ID | P | Deliverable | Ref | Depends on |
|---|---|---|---|---|
| T‑6010 ✅ | P1 | Backups and restore drill: host-side encrypted `pg_dump` (version-matched, `gpg` AES‑256), retention, optional off-host upload hook, restore drill into a throwaway container with checks, runs recorded in the database and exposed as metrics, alert when backups stop | MVP §7, TR‑17, D19 | owner: off-host storage, key custody, RPO/RTO |
| T‑6020 | P1 | Load test: seed 100k listings, extend the k6 scenarios to search v2 and the public API, measure p95 against the NFRs, fix what misses | NFR‑PERF‑001/002 | — |
| T‑6030 | P1 | Security review pass: bandit and pip-audit locally, triage; role × endpoint permission matrix test; data scan for plaintext secrets/national IDs; pentest scope document | MVP §6.10, §6.12 | — |
| T‑6040 ✅ | P2 | `check_release` command: frozen modules off, required settings present, safe defaults; run by the deploy before switching | MVP §7, checklist §3 | — |
| T‑6050 | P2 | Capacity and recovery notes: RPO/RTO, PgBouncer, worker pool sizing from T‑6020 numbers; point-in-time recovery option (WAL archiving) | TR‑16, TR‑17 | T‑6020; owner targets |

## Order
T‑6010 → T‑6040 → T‑6030 → T‑6020 → T‑6050.

## Owner dependencies
- **Off-host backup storage** (an S3-compatible bucket or another server; possibly paid) and who holds the backup encryption key outside the server.
- **RPO/RTO targets** (PRD marks them `ASSUMPTION`). Daily dumps give an RPO of up to 24 hours; less needs WAL archiving (T‑6050).
- External pentest vendor (criterion 4).
