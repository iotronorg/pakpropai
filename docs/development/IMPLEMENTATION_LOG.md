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
- **T‑1070c (ADR‑023 addendum):**
  - `apps/properties/models.py`: `PriceHistory`, price snapshot in `from_db`, `_record_price_change`, atomic `save()`, snapshot refresh only for written fields (also for size).
  - New `apps/properties/price_history.py` (context).
  - Migrations 0029 and 0030.
  - `serializers.py`: `PriceHistorySerializer`.
  - `views.py`: `perform_update`, context in `perform_create`, `price_history` action.
  - `admin.py`: `save_model` context.
  - `ai/tools.py`: listing creation context.
  - Tests: new `tests/test_price_history.py` (10); `tests/test_size_v2.py` gained 1 test (17).

## Session 9 — 2026-11-23
- **T‑1070d (ADR‑023 addendum):**
  - New `apps/properties/taxonomy.py`: categories, subtypes, `property_type` ↔ `category` mapping, `subtype_errors`, drift SQL (`count_drift`, `sync_property_type_to_category`), and the amenity helpers (`parse_amenity_keys`, `active_amenity_keys`, `filter_all_amenities`).
  - `apps/properties/models.py`: `Category`, `SUBTYPE_CHOICES`, `category`/`subtype`/`amenities` fields, `(category, is_active)` index, taxonomy snapshot in `from_db`, `_sync_taxonomy`, `update_fields` extension, subtype rule in `v2_errors`. New models `Amenity` and `ListingAmenity`.
  - Migrations `properties.0031` (expand), `0032` (batched backfill), `0033` (seed, 35 amenities, en+ur), `0034` (concurrent index).
  - New command `sync_listing_taxonomy`.
  - `serializers.py`: `AmenityKeysField`, `_merge_taxonomy` (deprecated alias and conflict → 400), `_validate_amenities` (active, market, ≤60), atomic create/update, `AmenitySerializer`.
  - `views.py`: `category`/`subtype`/`amenities` filters (`amenity_keys_param`, ≤10), amenity prefetch (list, mine, recommend, similar), `PropertyTaxonomyView` (`GET properties/taxonomy/`, inline OpenAPI schema). `urls.py`: route added.
  - `search.py`: `SearchParams.amenities`, `resolve_amenities` (unknown keys dropped), `_scrapers_apply(purpose, amenities)`, DB amenity filter + prefetch, `amenities` in results. `scrapers/base.py`: `PropertyResult.amenities`.
  - `ai/_tools_search.py`: `amenities` tool argument; `ai/schemas.py`: `PropertySearchFilter.amenities`.
  - `admin.py`: `AmenityAdmin`, `ListingAmenityInline`, category/subtype columns.
  - Tests: new `tests/test_taxonomy_amenities.py` (23). `tests/test_markets.py`: the query count is now 2 (one constant amenity prefetch).
- **T‑1070e (ADR‑023 addendum):**
  - New `apps/markets/listing_schema.py` (`schema_errors`, `attribute_errors`).
  - `apps/markets/models.py`: `listing_schema` field, schema check in `clean()`.
  - `apps/markets/services.py`: `MarketInfo.listing_schema`, cache prefix `market:v2:`.
  - Migrations `markets.0003` and `0004`.
  - `apps/properties/models.py`: `attribute_schema_for`, `attribute_errors`, check in `clean()`.
  - `serializers.py`: `_target_market`, `_validate_attributes` (only when `attributes`/`country` is written).
  - Tests: new `tests/test_listing_schema.py` (9). `tests/test_listing_schema_v2.py`: its fixture `possession: 'ready'` is now `'available'`, because the PK schema rejects `'ready'`, as intended.
- **Owner decisions (2026‑11‑23)** recorded in DEVELOPMENT_STATUS and memory. They were then implemented:
- **KI‑050:**
  - `Property.from_db` snapshots `country`, and `save()` re‑derives `market` when it changes (`update_fields` extended).
  - `tests/test_markets.py`: the old "not recomputed" test now asserts the new rule.
  - New tests in `tests/test_listing_schema.py` (3).
- **T‑1033:**
  - `apps/compliance/middleware.py`: `_compile_allowlist` and `allowed_fields`; allow‑listed top‑level keys bypass masking, and the rest of the body is masked.
  - `config/settings/base.py`: `PRIVACY_MASK_FIELD_ALLOWLIST`; two path exemptions removed.
  - New `tests/test_pii_field_allowlist.py` (7).
- **T‑1070f:**
  - Migrations `properties.0035` (column + trigger), `0036` (backfill) and `0037` (concurrent GIN).
  - `models.py`: `search_tsv` and its index.
  - `text_search.py`: queries the column.
  - `tests/test_search_indexes.py`: new index name, plus the 0037 shape.
  - New `tests/test_search_tsv.py` (5).
  - 200k‑row EXPLAIN ANALYZE benchmark (scratch script, not kept).
- **T‑1032c:**
  - `apps/organizations/models.py`: `OrganizationInvite`; migration `organizations.0027`.
  - `membership.py`: `create_invite`, `respond_to_invite`/`_respond`, `revoke_invite`, `_lock_open_invite`, `_closed`, `_notify_invitee`.
  - `serializers.py`: `OrgInviteSerializer`, `OrgInviteResponseSerializer`.
  - `views.py`: members POST → invite; `OrgInviteListView`, `OrgInviteRevokeView`, `MyInvitesView`, `InviteRespondView`. `urls.py`: 5 routes.
  - Settings: `ORG_INVITE_TTL_DAYS`.
  - Tests: new `tests/test_org_invites.py` (12). `test_org_membership`, `test_membership_contract` (2) and `test_membership_consolidation` were updated for the accept step.

### 2026-11-23 — session 9 (third part): owner decisions, branding, T‑1060a
- **Decisions recorded:** T‑1070g approved for the next release; money stays in major units; no PostGIS or Google key yet; branding is RealTron.
- **Branding:** README, seed data and the settings comment now say RealTron AI. The PDF already did. The Meta OTP template must be re‑submitted by the operator.
- **T‑1060a (ADR‑024):**
  - new app `apps/locations`: `models.py` (`Location`), `services.py` (`normalise`, `match_location`, `merge`), `matching.py` (`link_listing`, `link_unmatched`, `report`), `admin.py` (review queue with verify/merge actions, `templates/admin/locations/merge.html`), `views.py`/`urls.py` (`GET /api/v1/locations/`), `serializers.py`, `tasks.py`, and the `match_listing_locations` command;
  - migrations: `locations 0001/0002` (seed), `properties 0038` (`location_ref`, `location_precision`) and `0039` (concurrent index);
  - properties: `LocationRefField` (write `location_id`), `LocationRefOut` (read `location_ref`), a market check, `?location_id=` with descendants, matching on create and on update when the text changes;
  - settings: the `apps.locations` app and the hourly beat `link-unmatched-listings`;
  - tests: `tests/test_locations.py` (19).

### 2026-11-23 — session 9 (fourth part): T‑1070g‑1 contract and staged g‑2
- **Added:**
  - `apps/properties/listing_contract.py` (trigger SQL, drift and invalid‑category repair, `status`/`problems` gate);
  - migrations `0040_listing_v1_state_removal` (state‑only removal + trigger) and `0041_repair_v1_drift` (non‑atomic);
  - the command `check_listing_contract`;
  - `apps/properties/contract_migrations/` (staged `0042`, `0043` (renumbered to `0045`/`0046` in T‑1060b), README);
  - `tests/test_listing_contract.py` (16).
- **Removed:**
  - the `Property` fields `area_marla`/`area_unit`/`area_sqm`/`property_type`, their index, `prop_fts_gin` and the dual‑write `_sync_size`/`_sync_taxonomy` (the model now has `_normalise_size_and_taxonomy`, `_compute_size_m2` as a Decimal, and a derived `property_type`);
  - `size_sync.py` and the `sync_listing_size`/`sync_listing_taxonomy` commands, with their tests.
- **Changed:**
  - serializers: `LegacyReadAliases`/`LegacyWriteAliases`, with alias mapping and conflict checks in `ListingV2ValidationMixin`;
  - taxonomy: `categories_for_property_type`, `property_type_expression`, `classify_type_text`; the drift SQL is gone;
  - views (`?type=`, market report), `reports/generator.py`, `reports/views.py`, `organizations/views.py`, `recommendations.py`, `search.py`, `ai/tools.py` (list_property create), `ai/context.py`, `seed_data`;
  - tests: factories (legacy kwargs translated), `test_size_v2`, `test_taxonomy_amenities`, `test_markets`, `test_discovery_engine`, `test_compliance_filters`, `test_payment_webhooks_deal_lock`, `test_copilot_stream`;
  - `apps/properties/tests.py`: an order‑dependent search test now sets the scraper flag explicitly.

### 2026-11-23 — session 9 (fifth part): T‑1060b geo search
- **Added:**
  - `apps/geo/` (`sql.py` trigger and GiST SQL, `search.py` with `within_radius`/`within_bbox`/`postgis_ready`/`bbox_for_radius`, the `geo_setup` command);
  - migrations `properties 0042_geo_point_expand`, `0043_backfill_geo_point`, `0044_geo_point_index`;
  - settings `GEO_POSTGIS_ENABLED` and `GEO_MAX_RADIUS_KM`;
  - `tests/test_geo.py` (12).
- **Changed:**
  - `Property`: `geo_lat`/`geo_lng` and `_public_point()`, set in `save()`;
  - serializers: `GeoPointMixin` (masked coordinates, `distance_km`, coordinate validation), `viewer_sees_exact_point`;
  - views: `apply_geo_filters`, `distance_km` ordering, and no early `return qs.none()` (that caused a 500 with `ordering=distance_km`);
  - `whatsapp/discovery_engine.py`: tier 2 now uses SQL;
  - `scripts/dev/test.sh`, `.github/workflows/ci.yml` and `docker-compose.yml` now use the PostGIS image;
  - the staged contract migrations were renumbered to 0045/0046.

### 2026-11-23 — session 9 (sixth part): T‑1060c geocoding
- **Added:**
  - `apps/geo/geocoding.py`, `apps/geo/tasks.py`, the `geocode_locations` command, `apps/geo/migrations/0001_centroid_fallback.py`;
  - `CREATE_TRIGGER_SQL_V2` in `apps/geo/sql.py` (v1 stays frozen for `properties 0042`);
  - `locations 0003_location_geocode`;
  - settings `GEOCODING_*` and `GOOGLE_MAPS_API_KEY`, and the beat job `geocode-locations`;
  - `tests/test_geocoding.py` (10).
- **Changed:**
  - `Property._public_point()` now has the centroid fallback, and `location_ref` is among the geo inputs;
  - `locations/matching.link_listing` refreshes the in‑memory point;
  - `LocationAdmin`: geocode columns and filter, manual coordinates, re‑geocode action.
- **Fixed during tests:** the coarse‑result check ignored nothing, so `{administrative_area_level_1, political}` passed. It now ignores the generic `political` tag.

### 2026-11-23 — session 9 (seventh part): T‑1080 listing lifecycle
- **Verified:** the owner committed T‑1060b/c (`52bd8bb`, `d991ee7`); the tree was clean.
- **Added:**
  - `apps/properties/lifecycle.py` (service, rules, expiry, reminders);
  - `apps/properties/lifecycle_sql.py` (frozen trigger/CHECK SQL);
  - `ListingTransition` model;
  - migrations `0045_listing_status_expand`, `0046_backfill_listing_status`, `0047_listing_status_index`;
  - task `expire_listings` with hourly beat;
  - setting `LISTING_EXPIRY_REMINDER_DAYS`;
  - `tests/test_listing_lifecycle.py` (15).
- **Changed:**
  - `Property`: `status`, `status_changed_at`, `published_at`, `expires_at`, `expiry_reminded_at`, `_sync_status`, `_record_transition`;
  - serializers: `status` in list/detail (read‑only) and create; `ListingTransitionSerializer`, `TransitionRequestSerializer`;
  - `PropertyViewSet`: status visibility, `?status=`, the `transitions` action, the create/patch lifecycle hooks;
  - property admin: lifecycle actions and a transitions inline;
  - OpenAPI type hints on `get_primary_image`/`get_url`.
- **Renumbered:** staged contract migrations to 0048/0049, depending on 0047; README and test updated.

### 2026-11-23 — session 9 (eighth part): T‑1011 MFA
- **Verified:** the owner committed T‑1080 (`4ec3d94`); the tree was clean.
- **Added:**
  - `apps/users/mfa.py`, `apps/users/mfa_views.py`, `apps/users/admin_mfa.py`, `templates/admin/login.html`;
  - models `MFADevice`, `MFARecoveryCode` and `User.mfa_enabled` (`users 0014_mfa`);
  - settings `MFA_ENFORCE`, `MFA_ISSUER`, throttle `mfa_verify`;
  - `tests/test_mfa.py` (14).
- **Changed:**
  - `JWTCookieOrHeaderAuthentication` checks the `mfa` claim, plus `MfaLenientAuthentication`;
  - the OTP, password, password‑reset and org‑registration logins go through `mfa.login_response`;
  - the token refresh refuses non‑MFA sessions for enrolled or required users, and inactive users;
  - user admin: `mfa_enabled` column and filter, *Reset MFA* action;
  - `UsersConfig.ready` installs the admin login form.
- **Found:** KI‑057 (cookie‑path CSRF failures are swallowed).

### 2026-11-23 — session 9 (ninth part): T‑1110 image upload hardening
- **Verified:** the owner committed T‑1011 (`1a1ed64`); the tree was clean.
- **Added:**
  - `apps/core/images.py` (`sanitize_image`, `safe_upload_name`, `SanitizedImageField`);
  - the `sanitize_images` command;
  - settings `IMAGE_*` and `MAX_IMAGES_PER_LISTING`;
  - `PropertyImage.thumbnail/alt_text/width/height/size_bytes/sha256` (`properties 0048`);
  - agent photo `upload_to` callable (`agents 0014`);
  - `tests/test_image_uploads.py` (11).
- **Changed:**
  - `upload_images` rewritten (all‑or‑nothing, duplicates, row lock, `can_manage`);
  - the `images/{id}` action gains PATCH; new `images/reorder`;
  - `PropertyImageSerializer` fields;
  - org logo and agent photo serializers;
  - `_property_image_path` and `_logo_upload_path` use `safe_upload_name`;
  - the agent photo content‑type‑only validator is removed.
- **Renumbered:** staged contract migrations to 0049/0050; the test now requires them to sit on the latest real migration.

### 2026-11-23 — session 9 (tenth part): T‑1120 LLM router and prompt registry
- **Verified:** the owner committed T‑1110 (`a215499`); the tree was clean.
- **Added:**
  - `apps/ai/llm.py`: router with provider order, circuit breaker, timeout, response cache, `AIInteraction` provenance, and no failover after a tool ran;
  - `apps/ai/prompts.py`: registry, market variables, market‑pack fragments, and the prompt library;
  - `apps/ai/backends/openai_compat.py`;
  - `complete()` on every backend, plus `Completion`;
  - settings `AI_PROVIDER_ORDER`, `AI_TIMEOUT_S`, `AI_BREAKER_*`, `AI_OPENAI_*`;
  - `AIInteraction.provider/prompt_name/prompt_version/prompt_digest` and new interaction types (`ai 0003`);
  - `tests/test_llm.py` (10 tests).
- **Changed:**
  - `apps/ai/scoring.py` (registered prompts; same signatures, optional `country`);
  - `apps/ai/agent.py` (chat and image analysis through the router; the agent's log row carries provenance);
  - `apps/ai/knowledge.py` (`agent.system`/`agent.image` registered; `get_market_knowledge` reads the registry);
  - `apps/leads/views.py` (summary and suggestions; the lead's phone is no longer sent);
  - `apps/campaigns/re_engagement.py`;
  - `backends.get_backend`/`generate` now delegate to the router.
- **Removed:** `apps/ai/client.py` (`GeminiClient`). Its breaker and cache are replaced by the router's.
- **Note:** `manage.py test` (no root `conftest`) leaks cached feature flags between tests, so `test_ai_side_effect_guards` fails there, also at HEAD. Use `scripts/dev/test.sh` (pytest).
- **New issue:** KI‑059 (OCR, STT and intent classifier not yet routed).

### 2026-11-23 — session 9 (eleventh part): T‑1032b‑1 membership phase C, expand
- **Verified:** the owner committed T‑1120 (`2bd9b9b`); the tree was clean.
- **Owner decision (asked):** stage it like T‑1070g, and keep `Agent.organization`.
- **Added:**
  - `membership.owner_of`, `owned_organization_ids`, `sync_admin_projection`, `set_owner`, `CONTRACT_BLOCKERS`;
  - `Organization.owner`;
  - `AgentOrgMembership.membership` plus `link_membership()` and save‑time derivation;
  - migrations `organizations 0028` (help text) and `agents 0015` (link FK and backfill; legacy model removed from the state; legacy table FKs dropped);
  - staged `organizations/contract_migrations/0029_drop_admin_user.py` and `agents/contract_migrations/0016_drop_agent_organization_memberships.py`, with a README;
  - `tests/test_membership_phase_c.py` (14).
- **Changed:**
  - Readers: org list/detail/me views, the org serializers (the `admin_user`/`admin_phone`/`admin_name` output shows the owner; input goes through `set_owner`), registration, external API key auth, notifications (broker network, agent application, compliance ×2, token governor, inventory conflicts, Stripe past‑due), `primary_membership`.
  - `transfer_ownership` no longer refuses a target who owns another org.
  - `find_drift`/`repair_drift`: new categories; `missing_owner` only for ownerless orgs.
  - Lead routing skips terms whose membership is inactive.
- **Tests updated:** the transfer conflict test now asserts that the transfer succeeds; the report‑only drift test gives the org a real owner; the two `AgentOrganizationMembership` model tests were removed.
- **Fixed during testing:** TRUNCATE failures from the unmanaged legacy table's FKs.

### 2026-11-23 — session 9 (twelfth part): T‑1040 lead/session sandbox flag
- **Verified:** the owner committed T‑1032b‑1 (`4ccd24f`); the tree was clean.
- **Added:**
  - `apps/organizations/sandbox.py`: `contact_is_sandbox` and the `SandboxFollowsOrg` mixin;
  - migrations `leads 0021_unscoped_leads_live` and `whatsapp 0013_unscoped_sessions_live`;
  - `tests/test_sandbox_contacts.py` (8).
- **Changed:**
  - `Lead` and `WhatsAppSession` use the mixin; their insert‑only `save()` overrides are removed.
  - `test_sandbox_visibility.test_unscoped_lead_keeps_default` became `test_unscoped_lead_is_live`.
- **Decision note:** treating unscoped contacts as live follows ADR‑002 ("records without an org are decided by the caller"; private‑client listings are live). There is no platform sandbox, so this was not escalated.

### 2026-11-23 — session 9 (thirteenth part): M2 start; T‑2010 explicit tool loop
- **Verified:** the owner committed T‑1040 (`46c2ae4`); the tree was clean.
- **M2 milestone file** `milestones/MILESTONE_02_AI_AGENT_MVP.md` created from PRD Phase 2 and the AI spec: 15 tasks, order, exit criteria = MVP acceptance 1–9. DEVELOPMENT_TASKS M2 section rewritten.
- **Added:**
  - `apps/ai/runtime.py` (`run_turn`, `TurnResult`, `TurnCommitted`);
  - `apps/ai/tool_registry.py` (`ToolSpec`, `REGISTRY`, `enabled_tools`, `validate_args`);
  - `Step`/`ToolCall` and `AIBackend.step`; `GeminiBackend.step` (AFC disabled) and `OllamaBackend.step` (also used by the OpenAI‑compatible provider);
  - setting `AI_MAX_TOOL_STEPS`;
  - `tests/test_runtime.py` (13).
- **Changed:**
  - `llm.chat` runs the runtime, with the refined failover rule; `LLMResult.tool_calls/steps`.
  - The agent offers registry specs (`_get_specs`); `_get_tools` is kept as a compatibility view.
  - Tool calls are stored in `AIInteraction.output_data`.
  - `tests/test_llm.py` fakes moved to `step()`.
- **Removed:** `GeminiBackend.chat` (AFC) and its history helper; the Ollama chat loop, `_run_tool`, `_TERMINAL_TOOLS`, `MAX_TOOL_ROUNDS`; the abstract `chat()`.

### 2026-11-24 — session 10: T‑2012 durable confirmations; T‑2011 AI traces and cost
- **Verified:** the owner committed T‑2010 (`9aa6cd8`); the tree was clean; baseline subset 68 passed.
- **T‑2012 added:** `PendingAction` (`ai 0004`), `apps/ai/proposals.py`, `pending_views.py`, `pending_urls.py`, `tests/test_pending_actions.py`; `confirmation.py` rewritten.
- **T‑2012 changed:** runtime gate; `@guarded` tools; `process`/`chat` `interactive` flag; router closes or withdraws proposals; `agent.system` prompt v2; erasure deletes pending actions.
- **T‑2012 removed:** `RealTronAgent._is_agent_request`/`_handle_agent_request`, `confirmation.mark_explicit_request`/`check`.
- **T‑2011 added:** `AITrace`, `ModelPrice` (`ai 0005`), `apps/ai/tracing.py`, `cost_views.py`, `tests/test_ai_traces.py`; per‑call usage records.
- **T‑2011 changed:** `views.py` (org scope, real prices); thinking/cached token capture; failover marked in `llm._run`; erasure unlinks traces.
- **Tests updated:** side‑effect guards and the deal‑lock AI tool tests confirm through `PendingAction`.

### 2026-11-24 — session 10, continued: T‑2013 grounding checker v2; KI‑060
- **Verified:** the owner committed T‑2011/T‑2012 (`25b2b5f`); the tree was clean.
- **Added:** `apps/ai/grounding.py`; `AITrace.grounding_codes`/`citations` (`ai 0006`); `tests/test_grounding.py`.
- **Changed:** `runtime.run_turn` (evidence, `_ground`), `LLMResult`/trace fields, service step 5b, `search_properties` rows carry `currency`, inventory sample without prices and limited to visible listings, cost report counts fallbacks.
- **Fixed:** KI‑060 AML session block.

### 2026-11-24 — session 11: T‑2020 durable conversations; KI‑062, KI‑064
- **Verified:** the owner committed T‑2013/KI‑060 (`7cc4269`); the tree was clean.
- **Added:** `apps/conversations` (models, store, API, purge task, admin metadata), migrations `conversations 0001` and `ai 0007`, `tests/test_conversations.py`.
- **Changed:** `AIServiceManager.process` stores turns; `RealTronAgent` history reads the store; `conversation_key` follows the current conversation; `PendingAction.conversation`; search results and criteria recorded per turn; web chats do not trigger WhatsApp side effects; listing summary text says "phone number".
- **Fixed:** KI‑062, KI‑063 residual, KI‑064. **Found:** KI‑065, KI‑066.

### 2026-11-24 — session 12: T‑2030 search criteria, search v2, tools; KI‑067
- **Verified:** the owner committed T‑2020 (`bcd33b6`); the tree was clean.
- **Added:** `properties/criteria.py`, `properties/listing_search.py`, `properties/search_views.py`, `ai/_tools_listings.py`, migration `conversations 0002`, `tests/test_listing_search.py`.
- **Changed:** tool registry and system prompt v3; runtime records criteria, results and matches; chat cards carry score, reasons and trade‑offs; the classifier no longer sends `purpose=buy` or `city=Unknown` when the user did not say; grounding accepts recorded deposits and service charges.
- **Tests edited:** names and prompt version in `test_runtime`, `test_ai_side_effect_guards`, `test_llm`, `test_ai_traces`; the web follow‑up test in `test_conversations` now checks the new tool.
- **Fixed:** KI‑067. **Found:** KI‑068, KI‑069, KI‑070.

### 2026-11-24 — session 13: T‑2031 public listing read API; KI‑070
- **Verified:** the owner committed T‑2030 (`1ae9c4d`); the tree was clean.
- **Added:** `properties/public_listings.py`, `public_views.py`, `public_urls.py`, `tests/test_public_listings.py`.
- **Changed:** `listing_search.summary` and `get_listing` redact private contacts; `execute(with_relaxations=)`; settings rate; URLs.
- **Fixed:** KI‑070 (`scripts/dev/test.sh`), and its earlier wrong diagnosis. **Found:** KI‑071.

### 2026-11-24 — session 14: T‑2050 enquiries, threads, email
- **Verified:** the owner committed T‑2031 (`74c959e`); the tree was clean.
- **Added:** `apps/enquiries` (models, services, views, URLs, migration), `apps/ai/_tools_enquiries.py`, `leads/0022`, outbox kind `notification.email`, `tests/test_enquiries.py`.
- **Fixed:** WhatsApp messages filed under another organization's lead (KI‑073); `upsert_lead` and `connect_to_agent` failing or crossing organizations when a person has several leads; `FRONTEND_URL` missing from settings; lead merge keeps threads.
- **Privacy:** erasure deletes enquiries and redacts replies; export lists enquiry facts and consents, not message text (KI‑066).

### 2026-11-24 — session 15: T‑2060 viewings
- **Verified:** the owner committed T‑2050 (`c17784e`); the tree was clean.
- **Added:** `apps/viewings`, `apps/ai/_tools_viewings.py`, `leads/0023`, `viewings/0001`, `tests/test_viewings.py`; appointment `no-show` and `feedback` actions.
- **Fixed (security):** an agent without an organization could create an appointment on **any** lead id and read that lead's phone number; an agent who changed organization took the old organization's visits along.
- **Fixed:** dashboard double booking; rescheduled visits were never reminded and could not be confirmed, completed or moved again; reminders ignored preferences; reschedule accepted past times and treated local times as UTC; the reminder promised CONFIRM/CANCEL replies nothing handled (KI‑076).
- **Privacy:** erasure cancels upcoming visits and removes notes, names and feedback; export lists visits.

### 2026-11-24 — session 16: T‑2040 favourites, saved searches, preferences
- **Verified:** the owner committed T‑2060 (`b2407db`); the tree was clean at the start.
- **Added:** `apps/preferences`, `apps/ai/_tools_preferences.py`, migrations `preferences/0001`, `notifications/0004`, `compliance/0004`, `tests/test_preferences.py`.
- **Changed:** search tools use stated preferences; `notify_user` returns a bool; `agent.system` 6; erasure and export.
- **Fixed during review:** `notify_user` swallows errors, so a failed alert would have marked listings as announced; a failed `remember_preferences` would have left the consent on.

### 2026-11-24 — session 17: T‑2041 comparison
- **Verified:** the owner committed T‑2040 (`65c6fe6`); the tree was clean.
- **Added:** `apps/properties/comparison.py`, `comparison_views.py`, `apps/markets/fx.py`, `FxRate` (`markets/0005`), `import_fx_rates`, tool `compare_listings`, `tests/test_comparison.py`.
- **Changed:** `agent.system` 7; grounding knows per‑listing derived amounts.
- **Fixed:** grounding read "10,000,000, PKR 150,000" as one amount, so the second was never checked (older bug).

### 2026-11-24 — session 18: T‑2014 moderation and owner decisions
- **Verified:** the owner committed T‑2041 (`5486a0e`); the tree was clean.
- **Added:** `apps/moderation` (two migrations), `apps/ai/_tools_moderation.py`, `apps/leads/privacy.py`, `DataExportFile` with status and download endpoints, `tests/test_moderation.py`, `tests/test_owner_decisions.py`.
- **Changed:** `list_property` saves a draft; WhatsApp photo texts; `agent.system` 8; `Procfile`; lead, message, appointment, activity and voice serializers.
- **Fixed:** a lister could archive a suspended listing and publish it again; merged leads kept the other lead's phone in the notes.

### 2026-11-24 — session 19: T‑2016 context‑free FAQ answers
- **Added:** `apps/ai/faq.py`, `tests/test_faq.py`. **Changed:** `apps/ai/service.py` no longer caches assistant replies.
- **Fixed:** city names matched inside words ("transfer" → San Francisco, "plan" → Los Angeles), so such messages became searches; the request filter refused sign‑in tokens containing `--` (KI‑089).

### 2026-11-25 — session 20: T‑2070 kill switch and eval gate
- **Verified:** the owner committed T‑2016 (`a33c7fa`); the tree was clean.
- **Added:** `AIKillSwitch` (`ai 0008`), `apps/ai/kill_switch.py`, `kill_switch_views.py`, `lang.py`, `apps/ai/evals/` (offline and live suites, data), commands `ai_kill_switch` and `run_ai_evals`, `.github/workflows/ai-evals-live.yml`, two test files.
- **Changed:** `service.py`, `llm.py`, `confirmation.py`, `agent.py`, WhatsApp router and speech-to-text check the switch; `GuardrailEngine.inspect` (pure) split out of `check_input`; CI runs the eval gate.
- **Fixed:** Urdu and Roman Urdu gaps in `guardrails.py`, `criteria.py` and `grounding.py` (see SESSION_HISTORY).

### 2026-11-25 — session 21: T‑2071 indirect prompt injection
- **Added:** `apps/ai/untrusted.py`, `apps/ai/evals/data/indirect.jsonl` and its suite, `tests/test_indirect_injection.py`.
- **Changed:** `runtime.py` (model's copy of tool results), `llm.render`, `context.py`, `grounding.py` (contact rule), `moderation/checks.py` (flag `assistant_instructions`); prompts `agent.system` 9, `agent.image` 2, `property.score` 3, `fraud.check` 3, `search.verdicts` 3, `document.ocr` 3, `lead.summary` 2, `lead.suggest_replies` 2, `campaign.reengage.user` 2.
- **Fixed:** `apps/geo/search.py` readiness cache right after boot.

### 2026-11-25 — session 22: T‑2051 thread photos, block, report, warnings
- **Added:** `apps/enquiries/safety.py`, models `MessageAttachment`, `EnquiryBlock`, `ThreadReport` (migration `enquiries 0002`), `apps/moderation/thread_views.py`, `tests/test_thread_safety.py`.
- **Endpoints:** multipart `POST threads/{id}/messages/`, `GET threads/{id}/attachments/{id}/`, `POST threads/{id}/report/`, `POST|DELETE threads/{id}/block/`; moderators: `moderation/threads/…`.
- **Changed:** enquiry creation and viewing reschedule respect blocks; overdue notice, erasure and export cover the new data.
- **Fixed:** `PIIMaskingMiddleware` and UUIDs; `core/idempotency.py` hashes uploaded files.

### 2026-11-25 — session 23: end-to-end journey (MVP criterion 1)
- **Added:** `scripts/e2e/{consumer_journey,seed,stub_server}.py`, `scripts/dev/e2e.sh`, `config/settings/e2e.py`, `.github/workflows/e2e.yml` (manual start; not yet run on GitHub), `tests/test_worker_queues.py`.
- **Changed:** `WA_API_URL` is a setting (same default); `Procfile` and `docker/entrypoint.worker.sh` name every queue.

### 2026-11-25 — session 24: form masking and provider callbacks
- **Added:** `apps/compliance/parsers.py` (default form parsers), `tests/test_form_masking.py`.
- **Changed:** masking helpers in `apps/compliance/middleware.py`; callback paths exempt from masking; stored payment callbacks masked (`masked_payload`); admin organization creation keeps `phone` and `email` (allow-list).
- **Fixed:** Jumio result read from Jumio; Stripe billing verification, event reading and retry.

### 2026-11-25 — session 25: MVP criteria 3 and 5
- **Added:** `tests/test_mvp_listings_alerts.py`.
- **Changed:** `Property.objects.visible_to()` also requires not expired and a market that is not inactive (`public_q` removed); consumer listing API, compare, trends and the WhatsApp location pin use it; listing detail hides owner data; reports check access; going live stamps `published_at`; alert backlog warning.
- **Fixed:** `GET /external/inventory/`.

### 2026-11-25 — session 26: data protection (MVP criteria 10 and 11)
- **Added:** `EncryptedJSONField`, `manage.py scan_plaintext`, `process_data_requests` (hourly), `DATA_REQUEST_SLA_DAYS` (30), `tests/test_mvp_data_protection.py`.
- **Migrations:** agents 0016, compliance 0006, inventory 0003, properties 0049, users 0015, verification 0009. The staged contract migrations depend on the new ones.
- **Changed:** one erasure for all requests, step by step; wider export; refresh tokens not stored; blacklist looked up by keyed hash; organizations may erase only their own customers.
- **Fixed:** the fraud-check report.

### 2026-11-25 — session 27: delivery measured and SLOs (MVP criteria 4 and 14)
- **Added:** `apps/core/slo.py` (objectives, outbox backlog collector, delivery report), `manage.py notification_delivery_report`, `Notification.event` (`notifications 0005`, `0006` concurrent index), metrics `realtron_notification_delivery_seconds`, `…_delivery_failures_total`, `realtron_outbox_deliveries_total`, `realtron_webhook_processed_total`, `realtron_webhook_queue_delay_seconds`; worker metrics server (`WORKER_METRICS_PORT`, `config/celery.py`); multiprocess export (`metrics_registry`); SLO recording rules and burn-rate alerts; `ops/tests/slo-rules.test.yaml` (promtool, CI step); `ops/alertmanager.yml`; Grafana provisioning and `ops/grafana/dashboards/realtron-slo.json`; `tests/test_slo.py` (30 tests).
- **Changed:** enquiry notifications carry `enquiry.new|message|reply`; the WhatsApp webhook counts every edge outcome and passes `received_at`; the inbound task records pickup delay and outcome; request-latency buckets include 0.3 s and 0.4 s, AI buckets 8 s; `ops/grafana-dashboard.json` moved to `ops/grafana/dashboards/realtron-overview.json` with a fixed data-source uid; worker image, entrypoints, `Procfile` and both compose files prepare the multiprocess directory; dev compose adds Alertmanager and keeps 35 days.
- **Fixed:** KI‑106 (worker and multi-worker metrics never exported; invalid webhook signatures never counted), KI‑107 (CIDR allow-list).

### 2026-11-25 — session 28: owner decisions on monitoring, alerts, export and file deletion
- **Added:** `docker/docker-compose.monitoring.yml`, `.env.monitoring.example`, `ops/alertmanager/{start.sh,alertmanager.email.yml.tmpl,alertmanager.yml}`, `ops/targets/` (dev) and `ops/targets.prod/`, `ops/dev-secrets/metrics_token` (dev only), `apps/compliance/cloud_files.py`, erasure step `cloud_files_deleted`, settings `PROMETHEUS_METRICS_TOKEN`, `WORKER_METRICS_ADDR`, `tests/test_owner_decisions_s28.py` (13 tests); CI renders and checks the email config.
- **Changed:** `ops/prometheus.yml` uses file discovery and a bearer token; `POST /compliance/export/` for every signed-in person (GET kept); `/metrics/` and `/health/` skip tenant resolution; a failed external-file step stops the erasure run; `ops/alertmanager.yml` moved to `ops/alertmanager/alertmanager.yml`; `.gitignore` ignores `ops/secrets/` and keeps `.env.monitoring.example`.
- **Fixed:** KI‑111 (internal host names got 404 from the tenant middleware). **Resolved:** KI‑085, KI‑108 and KI‑109 in the repository; KI‑105 in part.

### 2026-11-25 — session 29: runbooks (MVP criterion 15)
- **Added:** `docs/runbooks/` (README, llm-outage, whatsapp-token-expiry, data-incident, harmful-ai-output); `manage.py rotate_field_keys`, `manage.py revoke_sessions`; metrics for LLM provider failures, no provider answering, WhatsApp API errors by status and token checks; alerts `LLMUnavailable`, `LLMProviderFailing`, `WhatsAppTokenInvalid`, `WhatsAppAuthErrors`, `AIGuardrailBlocksSpike`, with promtool tests (60 rules).
- **Changed:** every alert has a `runbook` annotation; the token check covers organization tokens, treats 403 as invalid and emails admins; guardrail blocks are counted.
- **Fixed:** KI‑113 (outbox WhatsApp sends counted as sent when only buffered), KI‑115 (guardrail metric never counted). **Recorded:** KI‑114.
- **Tests:** `tests/test_runbooks.py` (14 tests).

### 2026-11-26 — session 30: deploys (MVP criterion 13)
- **Owner decisions:** Docker over SSH for staging and production; production approved by required reviewers of the GitHub `production` environment.
- **Added:** `.github/workflows/deploy.yml` (reusable, per-environment secrets, pinned host key), `scripts/deploy/remote_deploy.sh` (pull, migrate, start, wait for the new version on `/health/`, roll back), `docs/runbooks/deploy.md`, `APP_VERSION` in images and `/health/`.
- **Changed:** `ci.yml`: one build per commit → staging → production gate → production with the same tag; ECS jobs removed (`infra/scripts/health_check.py` deleted); `main` never cancelled; `pip-audit` gate; TruffleHog pinned by commit; the fake migration-rollback step removed. Smoke test requires `ok`, the deployed version and a refused unsigned webhook.
- **Fixed:** KI‑116 (health leaked error text), KI‑117 (HTTPS redirect broke internal probes and scraping), KI‑118 (14 vulnerable packages; DRF 3.18 needed `validators = []` on the kill-switch serializer), KI‑119 (prod entrypoint ignored commands; unwritable static directory), KI‑120 (compose read the wrong `.env`).
- **Tests:** `tests/test_deploy_pipeline.py` (17 tests); local rehearsal with a registry and the real images.

### 2026-11-26 — session 31: visitors change or cancel viewings in the chat (KI‑076), deal-lock error cause (KI‑088)
- **Added:** tools `my_viewings`, `cancel_viewing`, `reschedule_viewing` (`apps/ai/_tools_viewings.py`; registry side effect `booking`, both changes confirmation-gated); confirmation summaries `_cancel_viewing`, `_reschedule_viewing` (`apps/ai/proposals.py`) that fix the viewing id and the new UTC start; `V.CHANGE_TEXT` and `V.change_text()` (`apps/viewings/services.py`).
- **Changed:** system prompt `agent.system` v10 (tool guide and confirmation list); visit reminders and the booking reply say "reply here" while `feature_talk_to_agent` is on; the E2E journey (`scripts/e2e/`) moves and cancels the viewing after booking, on the web assistant and WhatsApp.
- **Fixed:** KI‑088: the deal-lock view reports "already locked" only for `unique_active_deal_lock_per_property`; any other `IntegrityError` is logged with its constraint name and raised.
- **Verified:** KI‑092's web verification path does not run (`run_verification_task` has no caller); recorded on the KI.
- **Tests:** `tests/test_viewings.py::ChangeToolTest` (6 tests), `tests/test_deal_lock.py::…::test_only_the_active_lock_constraint_means_already_locked`; prompt pins in `tests/test_llm.py`, `tests/test_ai_traces.py`.
- **Continued (same session):** KI‑083 (ADR‑055): `apps.leads.privacy.hidden_session_phones` / `session_phone`; the agent room's `connected` list and `inbound_message` events hide withdrawn numbers and drop the raw `from`. KI‑121: copilot tax sheet uses the person's lead in this organization and its budget (fields that exist); document links left off. KI‑087: `SemanticResponseCache` and embedding helpers deleted with `tests/test_semantic_cache.py`; `sentence-transformers`, `numpy` removed from `requirements/base.txt`. Tests: `test_owner_decisions.py::WithheldTest::test_live_chat_streams_hide_the_number`, `test_copilot_stream.py::…::test_tax_sheet_uses_the_persons_lead_here`.

### 2026-11-26 — session 32: M3 started; agent performance (T‑3010), tasks and follow-ups (T‑3020)
- **Added:** `milestones/MILESTONE_03_PROFESSIONAL_WORKSPACE.md`; `apps/reports/performance.py` and `GET /api/v1/reports/agent-performance/` (ADR‑056); `LeadTask` (`leads 0024`), `apps/leads/follow_ups.py`, `apps/leads/task_views.py` (`/api/v1/leads/tasks/`), task `send_lead_task_reminders` and beat `lead-task-reminders`, erasure step `lead_tasks_redacted` (ADR‑057); enum overrides `LeadTaskKindEnum`, `LeadTaskStatusEnum`.
- **Tests:** `tests/test_agent_performance.py` (7), `tests/test_lead_tasks.py` (9); `tests/test_mvp_data_protection.py` covers `LeadTask`.

### 2026-11-26 — session 33: listing analytics (T‑3060)
- **Added:** `ListingDailyStat` (`properties 0050`); `apps/properties/analytics.py` (`record`, `record_on_commit`, `shown`, `detail_viewed`, `detail_viewed_in_chat`, bot filter, once-a-day keyed-hash cache key); `apps/reports/listing_performance.py` and `ListingPerformanceView` (`GET /api/v1/reports/listing-performance/`) (ADR‑058).
- **Changed:** counting hooks in `apps/properties/public_views.py` (search, detail), `apps/properties/search_views.py`, `apps/properties/views.py` (`PropertyViewSet.retrieve`), `apps/ai/_tools_listings.py` (`search_listings`, `get_listing`), `apps/preferences/services.keep` (new saves), `apps/enquiries/services.create` (new threads). Staged contract migrations renamed `0051_drop_listing_v1_columns`, `0052_drop_prop_fts_gin` (dependency on 0050); README and `tests/test_listing_contract.py` follow.
- **Tests:** `tests/test_listing_analytics.py` (19).

### 2026-11-26 — session 33 (continued): AI listing assistant (T‑3050)
- **Added:** `apps/ai/listing_assistant.py` (`missing_info`, `describe`, `extract`, `check_sentence`, prompts `listing.describe` / `listing.extract` v1); `apps/properties/assistant_views.py` and routes `/api/v1/properties/{id}/ai/missing-info|describe|extract/`; config flag `feature_listing_assistant`; throttle scope `listing_assistant` (`LISTING_ASSISTANT_RATE`); `AIInteraction.InteractionType.LISTING_ASSIST` (`ai 0009`); eval suite `listing_copy` (`apps/ai/evals/offline.py`, `data/listing_copy.jsonl`) (ADR‑059).
- **Tests:** `tests/test_listing_assistant.py` (15); `tests/test_llm.py` pins the two prompts.

### 2026-11-26 — session 33 (continued): unified audit trail (T‑3070)
- **Added:** `AuditEvent` (`core 0004` with trigger `audit_events_guard`); `apps/core/audit.py` (`record`, `safe_record`, `verify`, `redact_person`, mirrors and `connect_mirrors` from `CoreConfig.ready`); `apps/core/audit_views.py` (`/api/v1/audit-events/`, `/verify/`); `manage.py verify_audit_chain`; task `apps.core.tasks.verify_audit_chain` and beat `verify-audit-chain`; metrics `realtron_audit_write_failures_total`, `realtron_audit_chain_ok`; setting `AUDIT_CHAIN_KEY`; erasure step `audit_events_redacted` (ADR‑060).
- **Changed:** `AuditLog.log` builds and saves the row itself so the mirror knows the target's organization.
- **Tests:** `tests/test_audit_trail.py` (14, including a six-thread writer test and tampering with the trigger disabled).

### 2026-11-26 — session 33 (continued): AI ops console APIs (T‑3090)
- **Added:** `AIEvalRun` (`ai 0010`); `apps/ai/ops.py` (window, prompts, prompt detail, models, tools, eval runs, traces, overview); `apps/ai/ops_views.py` with ten routes under `/api/v1/ai/ops/` in `apps/ai/urls.py` (ADR‑061). Price additions and eval runs go to the audit trail.
- **Tests:** `tests/test_ai_ops.py` (11).

### 2026-11-26 — session 33 (continued): pipeline stages (T‑3030)
- **Added:** `PipelineStage`, `Lead.stage`, `Lead.stage_changed_at`, `LeadActivity.ActionType.STAGE` (`leads 0025`); `apps/leads/pipeline.py` (stages with defaults, current stage, counts, add, update, reorder, archive, move, board); `apps/leads/pipeline_views.py` and routes under `/api/v1/leads/pipeline/`; `LeadViewSet.move_stage`; `stage` on `LeadSerializer` (ADR‑062).
- **Tests:** `tests/test_pipeline_stages.py` (11).

## 2026-11-26 — Session 34

### T‑3040 Team management and assignment rules (ADR‑063)
- **Added:** `AssignmentRules`, `AgentRouting` (`leads 0026`); `apps/leads/assignment.py` (rules with defaults, `update_rules`, `org_agents`, `agent_org_ids`, open-lead counts, `update_routing`, `team`, lead language and areas, `rank`, `auto_assign`); `apps/leads/assignment_views.py` and routes `/api/v1/leads/assignment/rules/`, `team/`, `team/<agent_id>/`.
- **Changed:** `apps/leads/services.py` — `suggest_agents_for_lead` ranks through the rules (signature kept, optional `rules`); `assign_agent_to_lead` sets `routing_state`, counts `total_leads`/`last_active_at`, records the rule, `notify=False` for batches. `apps/leads/tasks.py` job and `LeadViewSet.auto_assign` use `assignment.auto_assign` (locks lead and rules; round-robin cursor). `LeadViewSet.assign` and `BulkAssignLeadsView` take only agents who work for the lead's organization; bulk: ≤200 ids, UUID check, per-lead activity, one notification.
- **Tests:** `tests/test_assignment_rules.py` (14); existing `test_auto_assign.py` and `test_service_layer.py` pass unchanged.

### T‑3100a Unified contact and messages, expand phase (ADR‑064)
- **Added:** `leads 0027` (`CRMContact.user`/`updated_at`, blank `phone`, partial uniques, `link_contacts` data step, view `unified_messages`); unmanaged `UnifiedMessage`; `apps/leads/contacts.py` (sessions by person and organization, lead/contact message querysets, cursor paging, people list with search and tags, detail, `annotate`); `apps/leads/contact_views.py` and routes `/api/v1/leads/contacts/`, `contacts/<user_id>/`, `contacts/<user_id>/messages/`; `LeadViewSet.messages` (`/leads/<id>/messages/`).
- **Changed:** `summarize` and `suggest-replies` read the unified list (`_lead_log`); `/conversations/` documented as deprecated; `ClientProfile` marked deprecated; erasure deletes `CRMContact` by person as well as phone; export gains `agency_contact_notes`; `RELATIONS` in `tests/test_mvp_data_protection.py` gains `leads.CRMContact.user`, and the erasure fixture fills a contact note.
- **Tests:** `tests/test_unified_contacts.py` (9).

### T‑3100b One message log (ADR‑064 addendum)
- **Added:** `leads 0028` (view v2 around `ConversationMessage.session`/`organization`/`msg_type`/`media_id`/`media_url`, optional `lead`, unique `(session, wa_message_id)`); `UnifiedMessage.session` (FK without constraint) and `media_url`; `apps/whatsapp/history.py`; `apps/leads/backfill.py`; `manage.py backfill_contact_messages`.
- **Changed writers:** `MessageRouter._log_inbound`/`_send_and_log` (one row; `_persist_crm_*` removed; `_crm_lead` delegates), `AgentRoomConsumer._send_whatsapp`, `LeadViewSet.send_message`.
- **Changed readers:** `whatsapp/client.is_within_24h_window`, `whatsapp/tasks.process_copilot_recommendations`, `whatsapp/views.NotificationDetailView` (ids are the stored rows' ids), `reports/views` bot report, `campaigns/re_engagement.LeadContextBuilder._wa_transcript` (own organization only), `ml/dataset_curator`, `compliance/tasks` export, `compliance/erasure` (`_cloud_files`, `_whatsapp`), `leads/contacts` (session match for every row).
- **Tests:** `tests/test_message_log.py` (8); `tests/test_enquiries.py::CrmIsolationTest` and `tests/test_unified_contacts.py` updated to the single writer.

### T‑4010 Investment calculator (ADR‑065)
- **Added:** `apps/properties/investment.py` (`calculate`, `comparable_rent`, `numbers`), `apps/properties/investment_views.py`, `apps/properties/investment_urls.py` (`/api/v1/investment/`), `apps/ai/investment_commentary.py` (prompt `investment.commentary` v1, `check`, `commentary`); `AIInteraction.InteractionType.INVESTMENT_COMMENTARY` (`ai 0011`); flag `feature_investment_commentary` (off); throttles `investment`, `investment_commentary`; prompt module registered in `apps/ai/ops.py`.
- **Tests:** `tests/test_investment.py` (9).

### T‑4020 Indicative price context (ADR‑066)
- **Added:** `apps/properties/price_context.py` (`context`, `for_listing`, `disclaimer`), `apps/properties/price_context_views.py` (`/api/v1/price-context/`), throttle `price_context`.
- **Changed:** `apps/audit/services.py` (`_listing_comparables`, report key `listing_comparables`), `apps/audit/pdf.py` (`_comparables_block` on page 1), `config/urls.py`.
- **Tests:** `tests/test_price_context.py` (6).

### T‑4030 Comparison narrative and running costs (ADR‑067)
- **Added:** `apps/ai/comparison_narrative.py` (prompt `comparison.narrative` v1, `cells`, `table`, `unknowns`, `check`, `narrative`), `CompareNarrativeView` (`/api/v1/compare/narrative/`), `AIInteraction` type `comparison_narrative` (`ai 0012`), flag `feature_comparison_narrative`, throttle `comparison_narrative`.
- **Changed:** `apps/properties/comparison.py` (`running_costs`, item key), `comparison_views.py` (serializer field), `apps/ai/ops.py` (prompt module), `config/urls.py`.
- **Tests:** `tests/test_comparison_narrative.py` (6).

### T‑4060 Offers register (ADR‑068)
- **Added:** `LeadOffer`, `LeadActivity.ActionType.OFFER` (`leads 0029`), `apps/leads/offers.py`, `apps/leads/offer_views.py` (`/api/v1/leads/offers/`), task `expire_lead_offers` + beat `lead-offer-expiry`.
- **Changed:** `apps/compliance/erasure.py` (`lead_offers_redacted`), `apps/compliance/tasks.py` (export `offers`), `apps/leads/urls.py`, `tests/test_mvp_data_protection.py` (relation coverage).
- **Tests:** `tests/test_lead_offers.py` (6).

### T‑4050 Duplicate clusters and fraud signals v2 (ADR‑069)
- **Added:** `apps/properties/duplicates.py`, `apps/properties/signals.py` (+ `PropertiesConfig.ready`), `DuplicateCluster`, `Property.duplicate_key/duplicate_cluster/duplicate_distinct`, `PropertyImage.dhash` (`properties 0051`), `apps/core/images.dhash/hamming`, tasks `refresh_duplicate_cluster`, `rebuild_duplicate_clusters` + beat, command `rebuild_duplicate_clusters`, `apps/moderation/duplicate_views.py`.
- **Changed:** `apps/core/images.sanitize_image` (dHash), photo upload stores it, `apps/moderation/checks.run` (signals v2), `apps/moderation/urls.py`.
- **Tests:** `tests/test_duplicates.py` (7); `PLAIN` allow-list entry for `Property.duplicate_key`; staged contract migrations renumbered to 0052/0053 (`tests/test_listing_contract.py`).

### T‑4051 Duplicate clusters collapsed in consumer search (ADR‑070) — session 35
- **Changed:** `apps/properties/listing_search.py` (`collapse_default`, `_group`, `_collapsed` window subquery, `_also_listed`, `copy_summary`; `Hit.also_listed/also_listed_count`; `execute(collapse_duplicates=)`; `_digest` binds cursors to collapsed vs full; `total` counts groups), `apps/properties/search_views.py` (`ListingCopySerializer`, hit fields, `collapse_duplicates` body field), `apps/properties/public_views.py` (hit fields, `collapse_duplicates` query parameter), `apps/ai/_tools_listings.py` (`also_listed_count`, `duplicates_note`), `config/settings/base.py` (`SEARCH_COLLAPSE_DUPLICATES`).
- **Tests:** `tests/test_search_duplicates.py` (11): collapse and copies, hidden prices never order the copies, shown copy by sort vs moderator canonical, invisible/non-matching copies, cursor walk in every sort, cursor binding, setting, cap, REST/public API and validation, private seller without name, AI tool count only.

### KI‑125 fix: slot ids masked as card/account numbers — session 35
- **Root cause:** found from a kept traceback (`Unknown slot.` on a well-formed id) and a 4000-id probe of `PIIMaskingPipeline`: 7 ids altered.
- **Changed:** `apps/compliance/middleware.py` (`_MACHINE_ID`: UUID or `<uuid>@YYYYMMDDTHHMMZ`, full match only; anything longer is still masked).
- **Tests:** `tests/test_viewings.py::BookTest::test_slot_ids_are_never_masked_as_card_or_account_numbers` (the exact masked id; a longer string still masked; an API booking under a masker that flags every string with a digit).

### KI‑126 (alerts half): saved-search alerts skip copies — session 35
- **Changed:** `apps/preferences/alerts.py` (`new_matches` excludes listings whose cluster has a member announced to the search or in the person's favourites/dismissals; `one_per_property` for one run; the "+" marker counts the batch before de-duplication).
- **Tests:** `tests/test_preferences.py::AlertTest` (+4): repost after an announcement, copies found together, copy of a saved or dismissed listing, setting off.

### T‑1092 Refusals raised with specific codes (ADR‑017 addendum) — session 35
- **Added:** `apps/core/errors.ApiError`; `attach_envelope` keeps the legacy `error` key for it; `apps/security/exception_handler.py` skips the auth-failure counter for it.
- **Changed:** `apps/verification/views.py` (14), `apps/users/views.py` (8 + 2 `code` keys; `_require_admin` raises), `apps/leads/admin_views.py` (6), `apps/properties/views.py` (5), `apps/audit/views.py` (2).
- **Tests:** `tests/test_error_envelope.py` (+4 `ApiErrorTest`, 1 expectation updated from `permission_denied` to `listing.admin_only`).
