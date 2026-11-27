# Development Status

_Last updated: 2026-11-27 (session 35; git commit subjects number sessions differently: `b1b5dd0` "session 66" is session 34 here)_

| Item | Value |
|---|---|
| Branch | `development`. The owner committed through KI‑126/T‑1092 (`61fb555`). **Uncommitted:** T‑1091 batch 1 (session 35, continued) |
| Current milestone | **M4 — AI intelligence** ([MILESTONE_04](milestones/MILESTONE_04_AI_INTELLIGENCE.md)). M3 is done apart from owner work (T‑3080, T‑3110) and the release‑gated T‑3100c. M2 open only T‑2072 (owner). M1 contracts release‑gated; M0 awaiting the first Actions run |
| M0 progress | All 17 tasks complete. Remaining exit criterion: CI green on GitHub Actions (owner pushes) |
| M1 progress | 16 of 18 tasks done; T‑1032b‑2 and T‑1070g‑2 staged (release‑gated); API debt: T‑1092 done, T‑1091 in progress (98 views left) |
| M2 progress | 16 of 17 tasks done. Open: T‑2072 (owner data and paid runs) |
| M3 progress | 8 of 11 tasks done + T‑3100a/b; T‑3100c waits for the production backfill; T‑3080, T‑3110 owner-blocked |
| M4 progress | 5 of 10 tasks done (T‑4010, T‑4020, T‑4030, T‑4060, T‑4050) + follow-up T‑4051. Rest owner-gated (T‑4040 embedding model, T‑4070 OCR vendor, T‑4080 POI licence, T‑4100 accounts) or needs volume (T‑4090) |
| Test suite | Last full run (session 35, end): **2682 passed, 1 skipped, 0 failed**. No migration drift; `manage.py check` clean; schema valid. See [TESTING_STATUS](TESTING_STATUS.md) |
| Last completed task | T‑1091 batch 1 (ADR‑016 addendum): verification, users, compliance and reports documented in the OpenAPI schema (152 → 98 views missing); `/auth/users/` split, so item methods on the collection are 405, not 500. Before it: T‑1092, KI‑126 alerts, T‑4051, KI‑125 |
| Next task | **Dev:** T‑1091 batch 2 — `apps/organizations/views.py` (11), `external_api.py` (5), `theme_views.py` (2), then agents (10), payments (9), whatsapp (9 + 3 provisioning), billing (8); same pattern (`apps/<app>/openapi.py`), ratchet the budgets in `tests/test_openapi_schema.py`. List what remains with a generator run (see IMPLEMENTATION_LOG T‑1091). T‑4040 waits for the owner's embedding-model choice. **Owner:** as before plus checklist §69, §70 (regenerate the frontend API client) |

## Owner decisions (2026-11-25, deploys)
- **Deploys:** Docker over SSH for staging and production. **Approval:** required reviewers on the GitHub `production` environment.

## Owner decisions (2026-11-25)
- **KI‑108:** alerts by **email**. **KI‑109:** **self-hosted Docker** monitoring stack, deployable anywhere. **KI‑085:** **any signed-in person** may export. **KI‑105:** 30-day SLA kept; **delete Cloudinary PDFs** on erasure.

## Owner decisions (2026-11-23)
- **T‑1032b:** staged like T‑1070g (b‑1 now, drops in a later release behind the reconcile gate); `Agent.organization` kept as a derived primary‑org pointer.
- **T‑1070g approved.** It ships one release after the current changes: drop `area_marla`/`area_unit`/`area_sqm`, the `property_type` column and `prop_fts_gin`.
- **Money stays in major units;** no minor‑unit conversion.
- **Geo infrastructure:** neither PostGIS nor a Google key exists yet. Build and test against a local PostGIS container with the Google client mocked; geocoding stays behind a flag that defaults to off.
- **Branding:** user‑facing text says **RealTron**.
- **T‑1060:** geocoding vendor **Google Maps Geocoding**; PostGIS required in every environment.
- **T‑1033 / KI‑032:** PII masking becomes a **per‑endpoint allow‑list**. Structured contact fields are allowed on lead/agent create, profile PATCH and member invite. Free‑text fields stay masked everywhere.
- **KI‑050:** `save()` **re‑derives `market` when `country` changes**.

## Owner decisions (2026-11-21)
- **No commits by the agent.**
- **Approved:** encryption (T‑0010), artifact removal (T‑0011), CI deploy‑job fixes (KI‑008).

## Open questions (non‑blocking)
- GitHub Actions has not run the updated workflow (no push).
- T‑1060a (location tree) is done and needs no new infrastructure. Before T‑1060b/c are deployed, the operator must provide PostGIS in every DB and `GOOGLE_MAPS_API_KEY`.
- Meta: re‑submit the OTP template body with the RealTron name.

## Operator actions before deploying these changes
- **Session 35** (checklist §69, §70): no migration; alerts skip copies of announced properties; ~35 refusals gain `detail` and specific `error_info.code`; `SEARCH_COLLAPSE_DUPLICATES` (default on); search hits gain `also_listed`/`also_listed_count`, `total` counts properties.
- **Session 34** (checklist §61–§68): migrations `leads 0026`, `0027`, `0028` `ai 0011`, `ai 0012`, `leads 0029` and `properties 0051` (then `manage.py backfill_contact_messages` until `--check` prints `pending=0`) (additive; `0027` creates the view `unified_messages` and links old contact rows); bulk/manual assign now refuse agents outside the lead's organization; later migrations touching the viewed columns must re-create the view.
- **Session 33** (checklist §56, §57): migrations `properties 0050` (additive) and `ai 0009` (choices only); staged contract migrations are now `0051`/`0052`; new prompts `listing.describe`/`listing.extract` (paid calls, `LISTING_ASSISTANT_RATE`); migration `core 0004` (audit trail, §58), fixed `AUDIT_CHAIN_KEY`, beat `verify-audit-chain`; migration `ai 0010` and model prices entered (§59); migration `leads 0025` (§60).
- **Session 32** (checklist §55): migration `leads 0024` (additive); restart celery beat for `lead-task-reminders`.
- **Session 31:** no migration. **Image rebuild:** `sentence-transformers` and `numpy` removed (torch no longer installed). Prompt `agent.system` is v10 (new viewing tools). Visit reminders now say "reply here" while `feature_talk_to_agent` is on. Run the staging E2E with the new move/cancel steps.
- **Session 30** (checklist §54): no migration. Set up servers and GitHub environments; add `localhost` to `ALLOWED_HOSTS`; check uploads and PDFs on staging after the dependency upgrades.
- **Session 29** (checklist §53): no migration. Reload the Prometheus rules; set a fixed `FIELD_HASH_KEY`; name the on-call, privacy owner and AI-stop roles in the runbooks.
- **Session 28** (checklist §52): no migration. Deploy `docker/docker-compose.monitoring.yml` with `.env.monitoring` and `ops/secrets/`; set `PROMETHEUS_METRICS_TOKEN` on the app; Cloudinary credentials on the erasure worker.
- **Session 27** (checklist §51): migrations `notifications 0005` (additive) and `0006` (concurrent index; direct DB connection). Scrape the workers on :9808, keep 35 days, choose an alert receiver.
- **Session 26** (checklist §50): **migrations that encrypt data; back up first.**
- **Session 25** (checklist §49): no migration; some API answers hide more.
- **Session 24** (checklist §48): no migration; provider callbacks behave differently.
- **Session 23** (checklist §47): no migration; the worker command now names every queue.
- **Session 22** (checklist §46): migration `enquiries 0002` (additive).
- **Session 21** (checklist §45): no migration. Prompt versions changed; review organization listings flagged `assistant_instructions`.
- **Session 20** (checklist §44): migration `ai 0008` (additive). Name who may stop the assistant.

See **[GO_LIVE_CHECKLIST](GO_LIVE_CHECKLIST.md)**: DB backup, `FIELD_ENCRYPTION_KEYS`, config‑row check. Summary:
- **Session 10** (checklist §32–§43): migrations `ai 0004`–`0007`, `conversations 0001`–`0002` (additive). Enter `ModelPrice` rows. Behaviour: agent hand‑off and audit requests now always ask for YES.
- **Session 9, continued** (checklist §18–§20): `properties 0035`–`0037` (stored search document; run before new pods serve), `organizations 0027` (invites). **API change:** member invites are now pending until accepted, so the frontend needs an accept flow. PII masking now uses per‑field rules on agent registration and member invites.
- **Session 9** (checklist §16, §17): migrations `properties 0031`–`0034` (taxonomy and amenities; `0034` builds its index concurrently, so run it on a direct DB connection) and `markets 0003`/`0004` (attribute schemas). Run `sync_listing_taxonomy` after rollout. **Behaviour change:** PK/AE/GB listing writes that send `attributes` now reject unknown keys and invalid values. Check the existing non‑empty `attributes` first (§17).
- **Session 8** (checklist §11 and **§12 UTC switch**): no migrations. Restart celery beat after deploy and review the periodic‑task rows (§12). **New: migrations `properties.0025`–`0028` (§13, §14), additive, rolling‑safe; run `sync_listing_size` after rollout (§14); `0029`/`0030` price history (§15).**
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
