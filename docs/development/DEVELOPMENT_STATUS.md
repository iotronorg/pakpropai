# Development Status

_Last updated: 2026-11-22 (session 8)_

| Item | Value |
|---|---|
| Branch | `development`. The owner committed through session 7 (`2fd8ee0`). **Session 8 (continued): T‑1070b is uncommitted** (the owner committed T‑1070a and earlier work) (the owner committed the earlier session‑8 work) |
| Current milestone | **M1 — Core foundation** ([MILESTONE_01](milestones/MILESTONE_01_CORE_FOUNDATION.md)); M0 code‑complete, awaiting first GitHub Actions run |
| M0 progress | All 17 tasks complete. Remaining exit criterion: CI green on GitHub Actions (owner pushes) |
| M1 progress | 9 of 18 tasks done (T‑1010, T‑1020, T‑1021, T‑1022, T‑1030, T‑1031, T‑1050, T‑1090, T‑1100) plus subtasks T‑1032a, T‑1070a, T‑1070b. T‑1060 blocked on a vendor decision; T‑1033 needs an owner policy sign‑off |
| Test suite | **1770 passed, 1 skipped, 0 failed** (session 8 end). See [TESTING_STATUS](TESTING_STATUS.md) |
| Last completed task | T‑1070b size rename + dual‑write (ADR‑023 addendum; KI‑049), after T‑1070a |
| Next task | **T‑1070c** `PriceHistory`: model (property FK, amount, currency, price_type, changed_at, changed_by, source), written in the same transaction whenever `price`/`currency`/`price_type` changes (create + update, via the `from_db` snapshot pattern of T‑1070b), read endpoint `properties/{id}/price-history/` with the same visibility as the listing, OpenAPI documented |

## Owner decisions (2026-11-21)
- **No commits by the agent.**
- **Approved:** encryption (T‑0010), artifact removal (T‑0011), CI deploy‑job fixes (KI‑008).

## Open questions (non‑blocking)
- Branding: PakProp vs RealTron (README and PDF footer unchanged).
- GitHub Actions has not run the updated workflow (no push).
- T‑1060 needs a geocoding vendor and PostGIS in every environment.
- **KI‑032 / T‑1033:** `PIIMaskingMiddleware` masks PII in every JSON write except a short exempt list. Which other endpoints (agent/lead create, profile PATCH) may receive raw phone/email needs an owner policy decision.
- **KI‑041 (new):** should an org invite need the invitee's acceptance (pending → accept/decline)? Today an existing user is added immediately.

## Operator actions before deploying these changes
See **[GO_LIVE_CHECKLIST](GO_LIVE_CHECKLIST.md)**: DB backup, `FIELD_ENCRYPTION_KEYS`, config‑row check. Summary:
- **Session 8** (checklist §11 and **§12 UTC switch**): no migrations. Restart celery beat after deploy and review the periodic‑task rows (§12). **New: migrations `properties.0025`–`0028` (§13, §14), additive, rolling‑safe; run `sync_listing_size` after rollout (§14).**
  - New member role‑change and ownership‑transfer endpoints; every member change is audited.
  - Alerts, reminders, escrow notices and the audit companion text now go through the outbox, so the `relay-outbox` beat job and an outbox worker must be running.
  - Listing prices are shown in the listing's own currency (KI‑044), and the seller deal‑lock code is sent only after AML screening (KI‑043).
- **Session 7** adds `markets 0001/0002` (seed), `properties 0023/0024` and `organizations 0025/0026` (`market` column + backfill). Run `market_backfill_report` afterwards. `DEFAULT_MARKET` (default `PK`) now drives model defaults. Org‑admin checks are now membership‑based and the members API refuses owner grants; run `reconcile_memberships` first. See checklist §10.
- **Session 6** adds `properties 0021` (`CREATE EXTENSION pg_trgm`) and `properties 0022` (non‑atomic, 4 × `CREATE INDEX CONCURRENTLY`; run off‑peak on a direct DB connection). `?search=` semantics changed (prefix match, no stemming); see checklist §9. New dependency `drf-spectacular` (image rebuild) and optional `OPENAPI_SCHEMA_PUBLIC`. Every response now has an `X-Request-ID` header, and `/api/` JSON errors gain `error_info` + `request_id` (additive). Session 6 also adds `core 0003` (idempotency table) and the beat job `purge-idempotency-records`.
- Session 4 adds `users 0013` (OTP hashing) and the worker command `replay_whatsapp_dead_letters`; see checklist §6.
- **Session 5 makes outbound WhatsApp actually work (KI‑033).** OTPs, notifications and alerts were never delivered before. Expect a jump in outbound volume; see checklist §8. Session 5 also adds `core 0002` (outbox) and the beat jobs `relay-outbox`/`purge-outbox`.
- Session 5 adds `organizations 0024` (membership backfill) and `reconcile_memberships`. Review `orphan_agent` rows by hand: ex‑team agents may still have org access. See checklist §7.
- Run migrations: `properties 0020`, `leads 0019` and `whatsapp 0011` are data backfills of `is_sandbox` only. Rows are not created or deleted, and the reverse is a no‑op.
- Check the `system_config` table. Any existing row overrides the new safe defaults: `scraper_search_enabled`, `feature_deal_lock`, `semantic_cache_enabled`.
- Expect platform demo listings and other orgs' sandbox listings to disappear from consumer search (intended, ADR‑002).
- WhatsApp listing and agent hand‑off now ask the user to reply **YES** before acting (ADR‑005).
