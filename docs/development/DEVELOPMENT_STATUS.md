# Development Status

_Last updated: 2026-11-21 (session 5)_

| Item | Value |
|---|---|
| Branch | `development` (session 5 work uncommitted; sessions 1–4 committed by the owner up to `b9ac8a7`) |
| Current milestone | **M1 — Core foundation** ([MILESTONE_01](milestones/MILESTONE_01_CORE_FOUNDATION.md)); M0 code‑complete, awaiting first GitHub Actions run |
| M0 progress | All 17 tasks complete. Remaining exit criterion: CI green on GitHub Actions (owner pushes) |
| M1 progress | 4 of 18 tasks done: T‑1010, T‑1020, T‑1030, T‑1021. T‑1060 blocked on a vendor decision; T‑1033 needs an owner policy sign‑off |
| Test suite | **1528 passed, 1 skipped, 0 failed** (session start 1477/1/0) — see [TESTING_STATUS](TESTING_STATUS.md) |
| Last completed task | T‑1021 transactional outbox (ADR‑014) + KI‑033 fix |
| Next task | **T‑1100** search indexes (milestone order), then T‑1090 API standards |

## Owner decisions (2026-11-21)
- **No commits by the agent.** Session 5 work is uncommitted on `development`.
- **Approved:** encryption (T‑0010), artifact removal (T‑0011), CI deploy‑job fixes (KI‑008).

## Open questions (non‑blocking)
- Branding: PakProp vs RealTron (README and PDF footer unchanged).
- GitHub Actions has not run the updated workflow (no push).
- T‑1060 needs a geocoding vendor and PostGIS in every environment.
- **KI‑032 / T‑1033:** `PIIMaskingMiddleware` masks PII in every JSON write except a short exempt list. Which other endpoints (agent/lead create, profile PATCH) may receive raw phone/email needs an owner policy decision.

## Operator actions before deploying these changes
See **[GO_LIVE_CHECKLIST](GO_LIVE_CHECKLIST.md)**: DB backup, `FIELD_ENCRYPTION_KEYS`, config‑row check. Summary:
- Session 4 adds `users 0013` (OTP hashing) and the worker command `replay_whatsapp_dead_letters`; see checklist §6.
- **Session 5 makes outbound WhatsApp actually work (KI‑033).** OTPs, notifications and alerts were never delivered before. Expect a jump in outbound volume; see checklist §8. Session 5 also adds `core 0002` (outbox) and the beat jobs `relay-outbox`/`purge-outbox`.
- Session 5 adds `organizations 0024` (membership backfill) and `reconcile_memberships`. Review `orphan_agent` rows by hand: ex‑team agents may still have org access. See checklist §7.
- Run migrations: `properties 0020`, `leads 0019` and `whatsapp 0011` are data backfills of `is_sandbox` only. Rows are not created or deleted, and the reverse is a no‑op.
- Check the `system_config` table. Any existing row overrides the new safe defaults: `scraper_search_enabled`, `feature_deal_lock`, `semantic_cache_enabled`.
- Expect platform demo listings and other orgs' sandbox listings to disappear from consumer search (intended, ADR‑002).
- WhatsApp listing and agent hand‑off now ask the user to reply **YES** before acting (ADR‑005).
