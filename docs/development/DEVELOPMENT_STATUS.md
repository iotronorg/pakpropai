# Development Status

_Last updated: 2026-11-21 (session 4)_

| Item | Value |
|---|---|
| Branch | `development` (nothing committed by the agent; all changes uncommitted) |
| Current milestone | **M1 — Core foundation** ([MILESTONE_01](milestones/MILESTONE_01_CORE_FOUNDATION.md)); M0 code‑complete, awaiting first GitHub Actions run |
| M0 progress | All 17 tasks complete. Remaining exit criterion: CI green on GitHub Actions (owner pushes) |
| M1 progress | 2 of 15 tasks done: T‑1010, T‑1020. T‑1060 blocked on a vendor decision |
| Test suite | **1477 passed, 1 skipped, 0 failed** (session start 1452/1/0) — see [TESTING_STATUS](TESTING_STATUS.md) |
| Last completed task | T‑1020 inbound WhatsApp retry/dead‑letter |
| Next task | **T‑1030** membership consolidation plan (ADR + migration plan for KI‑010), then T‑1031 RBAC |

## Owner decisions (2026-11-21)
- **No commits by the agent.** Session 4 work is uncommitted on `development` (sessions 1–3 were committed by the owner as `d283324`).
- **Approved:** encryption (T‑0010), artifact removal (T‑0011), CI deploy‑job fixes (KI‑008).

## Open questions (non‑blocking)
- Branding: PakProp vs RealTron (README and PDF footer unchanged).
- GitHub Actions has not run the updated workflow (no push).
- T‑1060 needs a geocoding vendor and PostGIS in every environment.

## Operator actions before deploying these changes
See **[GO_LIVE_CHECKLIST](GO_LIVE_CHECKLIST.md)**: DB backup, `FIELD_ENCRYPTION_KEYS`, config‑row check. Summary:
- Session 4 adds `users 0013` (OTP hashing) and the worker command `replay_whatsapp_dead_letters`; see checklist §6.
- Run migrations: `properties 0020`, `leads 0019` and `whatsapp 0011` are data backfills of `is_sandbox` only. Rows are not created or deleted, and the reverse is a no‑op.
- Check the `system_config` table. Any existing row overrides the new safe defaults: `scraper_search_enabled`, `feature_deal_lock`, `semantic_cache_enabled`.
- Expect platform demo listings and other orgs' sandbox listings to disappear from consumer search (intended, ADR‑002).
- WhatsApp listing and agent hand‑off now ask the user to reply **YES** before acting (ADR‑005).
