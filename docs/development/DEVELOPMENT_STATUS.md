# Development Status

_Last updated: 2026-11-26 (session 34; git commit subjects number sessions differently: `89982b9` "session 58" is session 33 here)_

| Item | Value |
|---|---|
| Branch | `development`. The owner committed T‑3030 (`89982b9`) and T‑3040 (`2a83b59`). **Uncommitted:** T‑3100a |
| Current milestone | **M3 — Professional workspace** ([MILESTONE_03](milestones/MILESTONE_03_PROFESSIONAL_WORKSPACE.md)), started 2026‑11‑26 (session 32). M2 is code complete; open there only owner work (T‑2072, T‑2080). M1 contracts release‑gated; M0 awaiting the first Actions run |
| M0 progress | All 17 tasks complete. Remaining exit criterion: CI green on GitHub Actions (owner pushes) |
| M1 progress | 16 of 18 tasks done (T‑1010, T‑1011, T‑1020, T‑1021, T‑1022, T‑1030, T‑1031, T‑1033, T‑1040, T‑1050, T‑1060, T‑1080, T‑1090, T‑1100, T‑1110, T‑1120) plus subtasks T‑1032a, T‑1032c, T‑1070a–f and T‑1070g‑1 |
| M2 progress | 16 of 17 tasks done. Open: T‑2072 (owner data and paid runs) |
| M3 progress | 8 of 11 tasks done (T‑3010, T‑3020, T‑3060, T‑3050, T‑3070, T‑3090, T‑3030, T‑3040) + T‑3100a; open T‑3100b/c; T‑3080, T‑3110 owner-blocked |
| Test suite | **2616 passed, 1 skipped, 0 failed** (after T‑3100a); no migration drift; `manage.py check` clean; OpenAPI budget test passes. See [TESTING_STATUS](TESTING_STATUS.md) |
| Last completed task | T‑3100a (ADR‑064): unified contact and messages, expand phase — view `unified_messages` over the CRM and WhatsApp logs (deduped; KI‑073 mirrors left out), contacts API `/api/v1/leads/contacts/…` (person ↔ leads, notes and tags in `CRMContact`), `/leads/<id>/messages/`; summaries and replies read it (`leads 0027`). Before it T‑3040 (ADR‑063) assignment rules |
| Next task | **Owner actions unblock the rest of the MVP** (unchanged): §54 servers/environments and first Actions run; §52 monitoring; §53 runbook roles; staging E2E; T‑2072; pentest; KI‑112, KI‑114 rule; new §61, §62. **Next dev task:** T‑3100b (ADR‑064 phase B): one writer table `messages`. Start in `apps/whatsapp/router.py` (`_log_inbound`, `_send_and_log`, `_persist_crm_*`), `apps/whatsapp/consumers.py` (`_send_whatsapp`), `apps/leads/views.py` (`send_message`), `apps/enquiries/services.py` (`post`), `apps/whatsapp/tasks.py` (copilot history), then the view in `apps/leads/migrations/0027_unified_contact_messages.py` (re-create as a UNION with the new table) and a batched backfill. Then T‑3080 when pricing exists |

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
- **Session 34** (checklist §61, §62): migrations `leads 0026` and `0027` (additive; `0027` creates the view `unified_messages` and links old contact rows); bulk/manual assign now refuse agents outside the lead's organization; later migrations touching the viewed columns must re-create the view.
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
