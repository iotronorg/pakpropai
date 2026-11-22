# Implementation Log

## 2026-11-21 — Session 2

### T‑0002 Test environment + baseline
- Added `scripts/dev/test.sh` (Docker, throwaway PG/Redis; ADR‑001).
- Added root `conftest.py` that clears the cache before each test (ADR‑007).
- `tests/test_compliance.py::test_export_endpoint_queues_task` now asserts the task is enqueued instead of calling Cloudinary.

### T‑0003 CI (partial)
- `.github/workflows/ci.yml` `security`/`test` jobs:
  - removed `working-directory: pakpropai`;
  - fixed the pip cache path;
  - branches `main` + `development` (was `develop`).
- `apps/inventory/sync_engine.py`: `md5(..., usedforsecurity=False)` clears Bandit B324 HIGH.
- Build/deploy jobs untouched.

### T‑0004 Sandbox integrity (ADR‑002)
- New `apps/organizations/sandbox.py` (`org_is_sandbox`).
- `Property`: `PropertyQuerySet.visible_to()` manager; `derive_is_sandbox()`; the flag is derived on insert.
- `Lead`/`WhatsAppSession.save()` derive on insert when an org is set.
- Migrations: `properties/0020`, `leads/0019`, `whatsapp/0011` backfill the flag (data only, reverse no‑op).
- `visible_to()` is applied in `PropertySearchService._from_db` (`viewer_org` parameter, `SearchParams.viewer_org_id`), the AI `search_properties` tool (`_ctx_org`), `similar_to`/`recommend_for_lead`, and the client `PropertyViewSet` list (client sees live + own).

### T‑0005 Semantic cache (ADR‑003)
- `apps/ai/service.py`: `semantic_cache_scope()`. The lookup now runs after classification. Nothing is stored on guard failure or canned fallback.
- SystemConfig default `semantic_cache_enabled=false`.

### T‑0006 Token accounting (ADR‑004)
- New `apps/ai/usage.py`.
- Recording added in the `gemini.py` (3 calls), `ollama.py` (chat + vision) and `client.py` (`GeminiClient.generate`) backends.
- `service.process` writes real totals to `record_token_usage`, `SlidingWindowTokenBudget` and the agent `AIInteraction` prompt/response tokens.

### T‑0007 Side‑effect guards (ADR‑005/006)
- New `apps/ai/confirmation.py`.
- `_tools_context.set_context(..., message=)` adds a turn id, the current message and an explicit‑request flag.
- Guarded `list_property` and `connect_to_agent`; `agent._handle_agent_request` marks an explicit request.
- **Bug fix:** `list_property` now sets `listing_owner_type='client'` and refuses when there is no user (KI‑021).
- Deal lock:
  - `feature_deal_lock` default `false`;
  - the tool refuses when off;
  - `DealLockInitiateView` returns 403 `feature_disabled`.
- Tests that create deals opt in via `tests/factories.enable_feature()`.

### T‑0008 Scrapers
- `scraper_search_enabled` default `false` (model DEFAULTS + service fallback).
- `live_search_pending` is true only when scraping is on. Removed user‑facing "Zameen/Graana" claims from the search messages, greeting menu and PK prompt.

### T‑0012 Routes/params
- `apps/properties/urls.py`: explicit paths before the router.
- `views._int_param` validation (400); compare `ids` UUID validation.

## 2026-11-21 — Session 3 (owner approved decisions; no commits)

- **T‑0010 encryption (ADR‑008):**
  - New `apps/core/crypto.py`, `apps/core/checks.py` and `apps/core/apps.py` (registers the check).
  - Settings: `FIELD_ENCRYPTION_KEYS`, `FIELD_HASH_KEY`, `FIELD_ENCRYPTION_ALLOW_DERIVED_KEY` (dev).
  - `requirements/base.txt` now declares `cryptography` (already installed transitively).
  - Model fields switched; `OrgWhatsAppConfig.verify_token_hash` + `find_by_verify_token`; webhook uses it plus `compare_digest`.
  - `SystemConfigService` keeps sensitive keys encrypted in the cache.
  - 7 migrations (schema + reversible data).
- **T‑0011:** removed `media/audits/*.pdf`, `ruvector.db`, `agentdb.rvf*` and `.claude-flow/*` from the working tree; `.gitignore` rules added.
- **CI (KI‑008):**
  - Build/deploy contexts set to `.` with `docker/Dockerfile.*`.
  - Smoke‑test working‑dir removed; frontend ECR build and ECS `frontend` service update removed.
  - New `infra/scripts/health_check.py`.
- **T‑0009:** `_tools_search._register_compliance_context`; `service.process` step 5b.
- **T‑0013:**
  - `AUDIT_DISCLAIMER`, `provenance_label()` and benchmark `_tag()` provenance in `apps/audit/services.py`.
  - PDF page‑1 notice and corrected final disclaimer; WhatsApp summary relabelled.
- **T‑0017:** audit tool confirmation guard; the direct route marks an explicit request. **Bug fix:** `PropertyAudit` field names (KI‑026).
- **T‑0015:** `FeatureEnabled` permission; `feature_voice_calls` / `feature_syndication` default off; voice and marketplace views gated.
- **T‑0014/16:** README corrected; `.env.example` keys; `GO_LIVE_CHECKLIST.md`.

## 2026-11-21 — Session 4 (M1 start; no commits)

- **M1 milestone file** `milestones/MILESTONE_01_CORE_FOUNDATION.md` created from PRD Phase 1 (tasks T‑1010…T‑1120, order, dependencies).
- **T‑1010 OTP hashing (ADR‑011):**
  - `apps/users/models.py`: `code` → `code_hash`; `hash_code`, `set_code`, `check_code`.
  - `apps/users/services.py`: `issue()` stores only the hash; `verify()` uses `check_code`.
  - `apps/users/admin.py`: list shows `purpose` instead of the code.
  - Migration `users 0013_otp_code_hash` (hash existing rows → drop column; reverse invalidates).
  - All existing `otp.code` callers (views, agent/org registration) unchanged — they use the instance returned by `issue()`.
- **T‑1020 inbound resilience (ADR‑012):**
  - New `apps/whatsapp/inbound_resilience.py` and `management/commands/replay_whatsapp_dead_letters.py`.
  - `apps/whatsapp/tasks.py`: task is bound, `acks_late`, `max_retries=6`; `_retry_inbound`.
  - `apps/whatsapp/client.py`, `router.py`: side‑effect markers.
  - `apps/whatsapp/views.py`: idempotency key released on dispatch failure.

## 2026-11-21 — Session 5 (M1; no commits)

- **T‑1030 membership consolidation (ADR‑013 phase A):**
  - New `apps/organizations/membership.py`:
    - `ensure_membership`, `deactivate_membership`;
    - `sync_agent_membership`, `sync_owner_membership`;
    - `remove_member`, `primary_membership`;
    - `find_drift`, `repair_drift`.
  - `apps/organizations/signals.py`: `post_init` snapshot plus `post_save` sync for `agents.Agent` (organization/user) and `organizations.Organization` (admin_user). It honours `update_fields` and `raw`, and reads `__dict__` so deferred fields cause no query.
  - `apps/organizations/views.py`:
    - org registration no longer creates the owner row itself (it would duplicate‑key against the hook);
    - members delete → `remove_member()`.
  - `apps/core/permissions.get_user_org` and `apps/whatsapp/middleware._get_org_id` → `primary_membership()`.
  - Migration `organizations 0024_backfill_missing_memberships` (create‑only, reverse no‑op).
  - Command `organizations/management/commands/reconcile_memberships.py` (`--apply`, `--show`, `--fail-on-drift`).
- **KI‑032:** `config/settings/base.py` `PRIVACY_MASK_EXEMPT_PATHS` += `/api/v1/organizations/me/members/`, `/api/v1/agents/register/`.
- **Tests:** new `tests/test_membership_consolidation.py` (25). `tests/test_org_membership.py` and `tests/test_lead_scoring.py` now seed owner rows with `get_or_create`.
- **T‑1021 transactional outbox (ADR‑014):**
  - `apps/core/models.py`: `OutboxMessage`; migration `core 0002_outbox_message`.
  - New `apps/core/outbox.py` (`enqueue`, `dispatch`, `relay_due`, `purge`, `HANDLERS`, `PermanentDeliveryError`) and `apps/core/tasks.py` (`dispatch_outbox_message`, `relay_outbox`, `purge_outbox`).
  - New `apps/notifications/delivery.py`: `enqueue_otp`, `deliver_otp`, `enqueue_notification`, `deliver_notification`.
  - OTP issue sites (`users/views.py` ×2, `organizations/views.py`, `agents/serializers.py`) → `enqueue_otp(otp)`.
  - `notifications/services.notify_user`: the Notification row and outbox row are written in one transaction.
  - `notifications/tasks.py`: `retry_failed_notifications` goes through the outbox; `send_otp_async`/`send_whatsapp_async` are kept as legacy shims.
  - Settings: `OUTBOX_CELERY_QUEUE` (default `default`), routes, beat `relay-outbox` (30 s) and `purge-outbox` (daily). `.env.example` documents it; the prod compose worker consumes `default,outbound`; the dev compose worker adds `outbound`.
- **KI‑033:** `WhatsAppClient.<method>(…)` → `get_wa_client(org).<method>(…)` in:
  - `leads/views.py`, `leads/signals.py`;
  - `verification/signals.py`;
  - `properties/tasks.py` (×2);
  - `escrow/tasks.py` (×3, via the `_wa(deal)` helper), `escrow/views.py`;
  - `notifications/services.py` (×2), `notifications/tasks.py` (×2).

  The `[OTP FALLBACK]` code logging was removed.
- **Tests:**
  - New `tests/test_outbox.py` (26).
  - Updated to outbox semantics: `test_celery_tasks.py` (2), `test_otp_hashing.py` (1), `test_service_layer.py` (1), `test_org_registration.py` (1).
  - Flaky `test_api_key_security.py::test_raw_token_not_stored` fixed (KI‑034).

## 2026-11-21 — Session 6 (M1; no commits)

- **T‑1100 search indexes (ADR‑015):**
  - New `apps/properties/text_search.py`: `SEARCH_CONFIG`, `SEARCH_FIELDS`, `search_vector()`, `build_prefix_tsquery()`, `apply_text_search()` (ref‑number exact path, sanitised prefix tsquery, `@@` filter + `ts_rank` ordering).
  - `apps/properties/models.py`: 4 new `Meta.indexes` (`prop_fts_gin`, `prop_city_trgm`, `prop_location_trgm`, `prop_active_rank_idx`).
  - Migrations `properties 0021_pg_trgm_extension` (extension kept on reverse) and `0022_search_indexes` (non‑atomic, `AddIndexConcurrently` ×4).
  - `apps/properties/views.py`: `?search=` → `apply_text_search` (was an unindexable `rank >= 0.01` filter).
  - `config/settings/base.py`: `django.contrib.postgres` in `INSTALLED_APPS`.
  - `GO_LIVE_CHECKLIST.md` §9.
- **Tests:** new `tests/test_search_indexes.py` (28).
- **T‑1090a OpenAPI (ADR‑016):**
  - `requirements/base.txt`: `drf-spectacular==0.29.0`.
  - `config/settings/base.py`: `drf_spectacular` app, `DEFAULT_SCHEMA_CLASS`, `SPECTACULAR_SETTINGS`, `OPENAPI_SCHEMA_PUBLIC`.
  - New `apps/core/openapi.py` (auth extensions, `SchemaAccessPermission`), imported in `CoreConfig.ready()`.
  - `config/urls.py`: `api/v1/schema/`.
  - `.github/workflows/ci.yml`: generate + validate + upload `openapi-schema` artefact.
  - `.env.example`: `OPENAPI_SCHEMA_PUBLIC`; `.gitignore`: `/openapi.yaml`.
- **Tests:** new `tests/test_openapi_schema.py` (11).
- **T‑1090b request id + error envelope (ADR‑017):**
  - `apps/core/middleware.py`: `RequestIdMiddleware`.
  - `apps/core/context.py`: `set_request_id`/`get_request_id`.
  - `apps/core/log_filters.py`: `record.request_id`.
  - New `apps/core/errors.py`: `attach_envelope`, `ErrorEnvelopeMiddleware`, `page_not_found`, `server_error`, `status_code_name`.
  - `apps/security/exception_handler.py`: auth‑failure logic moved to `_record_auth_failure`; the envelope is attached for every handled exception.
  - `config/settings/base.py`: both middlewares at the top of `MIDDLEWARE`, `request_id` in both log formats, `CORS_ALLOW_HEADERS`/`CORS_EXPOSE_HEADERS`, `ErrorEnvelope` schema component.
  - `config/urls.py`: `handler404`/`handler500`.
- **Tests:** new `tests/test_error_envelope.py` (20); `tests/test_openapi_schema.py` +1.
- **T‑1090c query‑param validation:**
  - New `apps/core/query_params.py`.
  - Replaced casts in `core/views.py` (audit log), `security/views.py` (chain verify, capped at 10 000), `observability/views.py` (hours 1–720), `verification/views.py` (fraud alerts), `notifications/views.py`, `whatsapp/views.py` ×2, `reports/bi_views.py` (top_n).
  - `properties/views.py`: `_int_param` removed in favour of the shared helpers; enum validation; price‑range check; `min_score` ≤ 100; `ref_no` exact (KI‑036); `@extend_schema_view` documents the list params.
- **Tests:** new `tests/test_query_param_validation.py` (18). `tests/test_verification_input.py::test_bad_limit_returns_400` now asserts the standard field error.
- **T‑1090d Idempotency‑Key (ADR‑018):**
  - `apps/core/models.py`: `IdempotencyRecord`; migration `core 0003_idempotency_record`.
  - New `apps/core/idempotency.py` (`idempotent`, `IDEMPOTENCY_PARAMETER`, `purge_expired`).
  - `apps/core/tasks.py`: `purge_idempotency_records`.
  - `@idempotent` + OpenAPI header on `leads/views.py` (`LeadViewSet.create`, `AppointmentViewSet.create`, both new thin overrides), `payments/views.py` (`CreateCheckoutView.post`, `SEPAPaymentView.post`) and `escrow/views.py` (`DealLockInitiateView.post`).
  - Settings: beat `purge-idempotency-records`, `IDEMPOTENCY_TTL_HOURS`, CORS header `idempotency-key`, exposed `Idempotent-Replayed`.
- **Tests:** new `tests/test_idempotency.py` (15).

## 2026-11-21 — Session 7 (M1; no commits)

- **T‑1050a Market entity (ADR‑019):**
  - New `apps/markets/models.py` (`Market`), `services.py` (`get_market`, `registry_info`, `market_code_for_country`, `invalidate`, `default_country/currency/area_unit`), `admin.py`, cache‑invalidation signals in `apps.py`.
  - New migrations: `markets 0001_market`, `0002_seed_markets`.
  - New command `markets/management/commands/market_backfill_report.py`.
  - `registry.py`: `MarketConfig.timezone`, `name`.
  - Literal defaults → callables: `properties`, `organizations`, `leads`, `payments`, `escrow`, `audit` models (state‑only migrations `audit 0012`, `escrow 0008`, `leads 0020`, `payments 0005`).
  - `Property.market` + `Organization.market` (`properties 0023/0024`, `organizations 0025/0026`), derived on create.
  - Settings: `DEFAULT_MARKET`.
- **Tests:** new `tests/test_markets.py` (24).
- **T‑1031a RBAC on memberships (ADR‑020):**
  - `apps/core/permissions.py`: `get_active_membership`, `get_user_org` (membership‑only), `active_org_role`, `_membership_role_by_id`, `_record_foreign_org_selector`; `IsAdminOrOrgAdmin`/`IsOrgAdmin`/`IsOrgMember` rewritten.
  - `apps/core/context.py`: requested‑org context var.
  - `apps/core/middleware.py`: `ActiveOrganizationMiddleware`; `TenantIsolationMiddleware` → `get_user_org`.
  - `apps/security/threat_detection.py`: `X-Organization-ID` removed from the override list.
  - `apps/organizations/views.py` `OrgMembershipView`: grant/removal policy.
  - `apps/users/views.py` `SendOTPView`: purpose whitelist (KI‑030).
  - Settings: middleware entry; CORS `x-organization-id`.
- **Tests:** new `tests/test_permission_matrix.py` (22).
- **T‑1031b legacy readers (ADR‑020 addendum):**
  - `apps/core/permissions.py`: `get_admin_org`, `primary_org`.
  - Switched: organizations (external_api, views), security/middleware, leads (serializers, admin_views), agents/views, compliance (utils, views, tasks, erasure), verification/views, inventory/views, billing (views, permissions), whatsapp (provisioning_views, views), reports/tasks.
  - `config/models.py`: `use_membership_rbac` default removed.
- **Tests:** `tests/test_permission_matrix.py` +4 (`MigratedReadersTest`). `tests/test_report_tasks.py::test_falls_back_to_user_org_when_no_property` modified: it now patches `primary_org` instead of mocking `owned_organization`.

## Session 8 — 2026-11-22
- **T‑1032a membership contract (ADR‑021):**
  - `apps/organizations/membership.py`: `actor_role`, `check_invite`, `invite_member`, `change_role`, `transfer_ownership`; `remove_member(actor=…, request=…)` enforces the owner/org‑admin rules itself. Each operation is atomic together with its `AuditLog` row. New exceptions `MembershipInvalid` (400), `MembershipForbidden` (403), `MembershipConflict` (409). A concurrent duplicate invite returns 409, not 500 (savepoint + `IntegrityError`).
  - `apps/organizations/views.py`: `OrgMembershipView` (GET/POST) + new `OrgMembershipDetailView` (PATCH/DELETE), thin wrappers over the service; new `OrgOwnershipTransferView`.
  - `urls.py`: `me/ownership/transfer/`, `<uuid:pk>/ownership/transfer/`.
  - `serializers.py`: schema‑only serializers for members and transfer; `@extend_schema` on every method.
  - `config/settings/base.py`: `ENUM_NAME_OVERRIDES` for the two `employment_type` choice sets.
  - `tests/test_openapi_schema.py`: budgets lowered 154/32 → 153/28.
- **KI‑040:** `apps/escrow/views.py::_check_high_risk_deal` now uses `AuditLog.log` with real fields; the buyer phone was dropped from the payload.
- **T‑1022a outbox `whatsapp.text` (ADR‑014 addendum):**
  - `apps/notifications/delivery.py`: `enqueue_whatsapp_text`, `deliver_whatsapp_text`.
  - `apps/core/outbox.py`: kind registered; payload is wiped after delivery.
  - Sites switched: `leads/signals.py` (hot lead), `verification/signals.py` (KI‑042), `properties/tasks.py` (listing alerts), `leads/tasks.py` (re‑engagement, stale reminders, appointment reminders; each in one transaction with its marker).
- **Tests:** new `tests/test_membership_contract.py` (37) and `tests/test_outbox_adoption.py` (11). Updated `tests/test_follow_up_automation.py` (13 tests) and `tests/test_celery_tasks.py` (1): they now assert the queued outbox row (key, recipient, org, expiry) instead of a mocked `get_wa_client`. Delivery through the real client is covered in `test_outbox_adoption.py`.
- **T‑1022b:**
  - `apps/escrow/tasks.py` (`_send`; status change and notices in one transaction);
  - `apps/escrow/views.py` (seller notice via the outbox, moved after AML screening — KI‑043; the exception log no longer includes message details);
  - `apps/audit/tasks.py` (companion text);
  - `apps/properties/tasks.py` (scraped follow‑up with `cache_key`; phone removed from the log);
  - `apps/notifications/services.py`: unused `send_whatsapp_message` removed.
  - Tests: `tests/test_outbox_adoption.py` +5.
- **T‑1050b‑1:**
  - `apps/markets/services.py`: helpers.
  - `PropertyResult.currency`; `properties/search.py` (`only('currency')`, `price_formatted` + `currency` in result dicts).
  - Formatting: `ai/_tools_search.py`, `ai/tools.py`, `ai/scoring.py`.
  - Fallbacks: `compliance/compliance_service.py`, `verification/views.py`, `properties/scoring.py`, `ai/service.py`, `ai/_tools_deals.py`, `ai/knowledge.py`, `ai/context.py`, `ai/agent.py`, `escrow/views.py`, `audit/views.py`.
  - Tests: `tests/test_markets.py` +5 and ratchet lowered.
- **T‑1050b‑2:**
  - `apps/markets/pk/__init__.py`: constants.
  - `apps/audit/services.py`: `cur`/`bench_cur`, `market_analysis.benchmark_currency`.
  - `apps/audit/pdf.py`: `_pkr` → `_money` with a `_REPORT_CURRENCY` context var set and reset by both entry points; KI‑046.
  - `apps/ai/tools.py`: PK constants and real currencies in the audit summary.
  - PK constants in `ai/schemas.py`, `properties/financial.py`, `payments/services.py`, `billing/gateway.py`.
  - Market‑neutral defaults in `ai/context.py` and `audit/views.py`.
  - Tests: `tests/test_markets.py` +5; ratchet 27 → 5.
- **T‑1050c (ADR‑022):**
  - `config/settings/base.py`: UTC, 3 crontabs.
  - `apps/markets/services.py`: time‑zone helpers.
  - Messages: `leads/tasks.py` (reminder), `leads/views.py` (appointment notice), `escrow/views.py` (expiry ×2), `verification/certificate.py` (date).
  - Buckets: `reports/views.py` (`_get_trend(tz)`, bot daily trend), `agents/stats.py`, `properties/views.py`, `leads/views.py` + `campaigns/public_views.py` (local day bounds).
  - `reports/tasks.py`: per‑org local month; `now` parameter; local PDF date.
  - Tests: new `tests/test_timezones.py` (10).
- **T‑1070a (ADR‑023):**
  - `apps/properties/models.py`: v2 fields, `Purpose`/`PriceType`, `v2_errors`, purpose index.
  - Migrations `0025_listing_schema_v2_expand` and `0026_property_purpose_index`.
  - `serializers.py`: `ListingV2ValidationMixin`; fields on list/detail/create.
  - `views.py`: filters and OpenAPI params.
  - `search.py`: `purpose_values`, `_scrapers_apply`, purpose/room filters, result fields.
  - `scrapers/base.py`: `PropertyResult` v2 fields; WhatsApp line shows beds, period, rent tag, price on request.
  - `ai/_tools_search.py`: `purpose`/`bedrooms` args, honest live‑pending.
  - `ai/schemas.py`: `bedrooms`, passed to the tool.
  - `ai/service.py`: bedroom regex.
  - Tests: `tests/test_listing_schema_v2.py` (16).
- **T‑1070b (ADR‑023 addendum):**
  - `apps/properties/models.py`: size v2 fields, `from_db` snapshot, `_sync_size`, `size_in`, `size_display`, `update_fields` extension.
  - Migrations 0027 and 0028.
  - New `apps/properties/size_sync.py` and `management/commands/sync_listing_size.py`.
  - `serializers.py`: size fields, deprecated alias help text, conflict check, `size_m2` read‑only.
  - `search.py`: m² band, result `size_value`/`size_unit`.
  - `scrapers/base.py`: DTO size fields + unit display.
  - Other readers: `scoring.py` (marla‑equivalent), `tasks.py` (alert size text), `ai/scoring.py` (prompt area), `whatsapp/copilot_stream.py` (`size_m2`, bedrooms), `seed_data.py`.
  - `config/settings/base.py`: `AreaUnitEnum` override.
  - Tests: `tests/test_size_v2.py` (16).
