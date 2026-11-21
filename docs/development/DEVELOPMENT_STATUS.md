# Development Status

_Last updated: 2026-11-21 (session 3)_

| Item | Value |
|---|---|
| Branch | `development` (nothing committed by the agent; all changes uncommitted) |
| Current milestone | **M0 — Stabilisation** ([MILESTONE_00](milestones/MILESTONE_00_STABILIZATION.md)) |
| M0 progress | **All 17 M0 tasks complete** in code and tests. Remaining exit criterion: CI must run green on GitHub Actions (needs a push, which the owner controls) |
| Test suite | **1452 passed, 1 skipped, 0 failed** (baseline 1361/1/1) — see [TESTING_STATUS](TESTING_STATUS.md) |
| Last completed task | T‑0015 freeze flags (voice calling, syndication) |
| Next task | M1 kickoff: T‑1010 OTP hashing (security), then T‑1020 WhatsApp outbox |

## Owner decisions (2026-11-21)
- **No commits.** All work stays uncommitted on `development`.
- **Approved:** encryption (T‑0010), artifact removal (T‑0011), CI deploy‑job fixes (KI‑008).

## Open questions (non‑blocking)
- Branding: PakProp vs RealTron (README and PDF footer unchanged).
- GitHub Actions has not run the updated workflow (no push).

## Operator actions before deploying these changes
See **[GO_LIVE_CHECKLIST](GO_LIVE_CHECKLIST.md)**: DB backup, `FIELD_ENCRYPTION_KEYS`, config‑row check. Summary:
- Run migrations: `properties 0020`, `leads 0019` and `whatsapp 0011` are data backfills of `is_sandbox` only. Rows are not created or deleted, and the reverse is a no‑op.
- Check the `system_config` table. Any existing row overrides the new safe defaults: `scraper_search_enabled`, `feature_deal_lock`, `semantic_cache_enabled`.
- Expect platform demo listings and other orgs' sandbox listings to disappear from consumer search (intended, ADR‑002).
- WhatsApp listing and agent hand‑off now ask the user to reply **YES** before acting (ADR‑005).
