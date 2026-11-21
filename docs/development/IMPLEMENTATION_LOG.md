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
