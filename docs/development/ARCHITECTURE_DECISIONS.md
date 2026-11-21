# Architecture Decisions (ADR log)

Product‑level decisions DEC‑01…DEC‑12 live in [../product/ai-real-estate-agent/PRD_CHANGELOG.md](../product/ai-real-estate-agent/PRD_CHANGELOG.md) and are treated as approved inputs. This file records **engineering** decisions made during implementation.

Format: ID · Date · Context · Problem · Options · Decision · Reasoning · Consequences · Rejected alternatives.

---

## ADR‑000 — Development milestones follow the PRD roadmap phases
- **Date:** 2026-11-21
- **Context:** The development brief suggests 11 generic milestones. The approved PRD roadmap ([PRODUCT_ROADMAP.md](../product/ai-real-estate-agent/PRODUCT_ROADMAP.md)) defines Phases 0–5 plus MVP launch criteria.
- **Problem:** Two competing milestone structures would cause tracking drift.
- **Options:** (a) the 11 generic milestones; (b) PRD phases 0–5 plus a cross‑cutting production‑readiness milestone.
- **Decision:** (b). M0 = Phase 0 Stabilisation, M1 = Phase 1 Core foundation, M2 = Phase 2 AI agent MVP (includes search/matching and core user workflows), M3 = Phase 3 Professional workspace, M4 = Phase 4 AI intelligence, M5 = Phase 5 Expansion, M6 = Production readiness (MVP §6–7 acceptance and launch criteria; security hardening runs inside every milestone).
- **Reasoning:** The documentation hierarchy puts the PRD and roadmap above generic guidance. The generic topics (globalisation, security, search) are all covered inside these phases.
- **Consequences:** Milestone files are named `MILESTONE_0X_<PHASE>.md`.
- **Rejected:** The 11‑milestone split would separate search from the AI agent, which the PRD deliberately ships together in Phase 2.

## ADR‑001 — Test execution environment: project Docker image + throwaway Postgres/Redis containers
- **Date:** 2026-11-21
- **Context:** The host has Python 3.15 and no Postgres. The suite uses Postgres‑specific features (full‑text search) and pinned Python 3.12 wheels.
- **Decision:** Run tests inside an image built from the repo `Dockerfile` (Python 3.12, `requirements/prod.txt`) on a private Docker network with `postgres:15-alpine` and `redis:7-alpine`. pytest and pytest‑django are installed in the test container only (matching CI), not added to project requirements. The procedure is documented in [TESTING_STATUS.md](TESTING_STATUS.md#how-to-run).
- **Reasoning:** Mirrors CI (`.github/workflows/ci.yml`), needs no host package installs, and is reproducible.
- **Consequences:** The first build is slow (torch via sentence‑transformers). Source is bind‑mounted so rebuilds are needed only when requirements change.
- **Rejected:** Host venv (no 3.12 interpreter, wheel gaps on 3.15); SQLite (unsupported Postgres features).

## ADR‑002 — Meaning of `is_sandbox` and listing visibility rules
- **Date:** 2026-11-21
- **Context:** `Property`, `Lead` and `WhatsAppSession` have `is_sandbox` (default `True`). It is flipped to `False` only once, by `ProductionProvisioner` at go‑live. Records created **after** go‑live therefore stay `True`, so the flag is inconsistent. Search, recommendations and the client listing API ignored it, so sandbox/demo listings of any org were visible to every consumer.
- **Options:** (a) drop the flag; (b) filter on the flag as‑is (would hide every post‑go‑live listing); (c) define the flag precisely, derive it on insert, backfill, then filter.
- **Decision:** (c).
  - A record **is sandbox** when it belongs to an organization whose `operational_mode != 'production'`, or when it is a `platform` (demo/internal) listing.
  - Listings owned by a private `client` or a `freelance_agent` (no org) are **not** sandbox.
  - New records derive the flag on insert from these rules (a production org's listings, leads and sessions get `False`).
  - A data migration backfills existing listings under the same rule. It is forward‑only on data, but reversible as a no‑op because only the derived flag changes.
  - Consumer‑facing listing querysets use `Property.objects.visible_to(viewer_org=…)`: `is_active` and (`is_sandbox=False` **or** belongs to the viewer's own org). This lets a sandbox org still test its bot on its own inventory.
- **Consequences:** Platform demo listings disappear from public search (intended). Org staff views (developer/agent scoped to their own org) are unchanged.
- **Rejected:** (a) loses the provisioning workflow; (b) would hide real inventory.

## ADR‑003 — Semantic response cache is opt‑in and limited to non‑personal intents
- **Date:** 2026-11-21
- **Context:** `SemanticResponseCache` matched any message by embedding similarity within an org and replayed the stored reply to any other user of that org. Replies are generated with lead context injected, and embeddings ignore numbers ("5 crore" ≈ "6 crore").
- **Decision:**
  - The cache is used only when SystemConfig `semantic_cache_enabled == 'true'` (default `false`), the intent is `general_query`, and the message has no digits.
  - The lookup moves after intent classification. The namespace is `org_id:language`.
  - Replies are not stored when the output guard rejected them or when the canned fallback was used.
  - The class API is unchanged.
- **Consequences:** Less cache hit rate; no cross‑user leakage by default. Enabling it safely needs the context‑free FAQ path (M2).

## ADR‑004 — Token usage captured from provider responses via a ContextVar
- **Date:** 2026-11-21
- **Decision:**
  - `apps/ai/usage.py` holds a per‑request accumulator.
  - Backends (`GeminiBackend`, `OllamaBackend`, `GeminiClient.generate`) record the provider‑reported usage; nothing is estimated.
  - `AIServiceManager.process` opens the capture and writes real numbers to `TokenUsageRecord`, the sliding‑window budget and `AIInteraction`.
- **Known limit:** With Gemini Automatic Function Calling, only the final response's `usage_metadata` is visible, so multi‑tool turns are under‑counted until the explicit tool loop (M2).

## ADR‑005 — Server‑side confirmation for side‑effect AI tools
- **Date:** 2026-11-21
- **Context:** The LLM could create listings and share the user's number with agents in one step. A `confirmed=True` tool argument would be trivially forged by the model.
- **Decision:** `apps/ai/confirmation.py` stores a pending proposal (tool + key params fingerprint + turn id) in the cache (TTL 30 min). The tool executes only when:
  - a matching proposal exists from an **earlier** turn, and
  - the **current** user message is an explicit short affirmative (EN/Roman Urdu/Urdu) with no negation.

  Proposals are single use, and a change in key parameters requires a fresh confirmation. Deterministic handlers acting on the user's literal request ("connect me to an agent") mark the turn as explicit. The guard covers `list_property` and `connect_to_agent`; `initiate_deal_lock` is frozen instead (ADR‑006).
- **Consequences:** One extra message per side‑effect action. Proposals are cache‑only; a durable `ActionConfirmation` model is planned for M2.

## ADR‑006 — Freeze deal lock (new deals only)
- **Date:** 2026-11-21
- **Decision:** `feature_deal_lock` defaults to `false`. While it is off:
  - The AI tool is not offered, and refuses if called directly.
  - `POST /api/v1/deals/lock/` returns 403 `feature_disabled`.
  - Confirm/cancel/release/dispute endpoints and payment webhooks stay available, so existing deals and money are not stranded.
  - Operators can re‑enable the flag explicitly.
- **Note:** A DB row in an existing environment overrides the default. Operators must check `system_config` before go‑live (see DEVELOPMENT_STATUS → Operator actions).

## ADR‑007 — Test isolation: clear the cache before every test
- **Date:** 2026-11-21
- **Context:** Test settings use the real Redis cache, and nothing reset it between tests. `SystemConfigService` caches config values, so a flag set in one test leaked into later tests after the DB rollback.
- **Decision:** A root `conftest.py` autouse fixture runs `cache.clear()` before each test. The full suite passed with it (1425 passed).

## ADR‑008 — Field‑level encryption (Fernet) with blind index for lookups
- **Date:** 2026-11-21 (approved by owner)
- **Decision:**
  - `apps/core/crypto.EncryptedTextField` stores values as `enc1:<Fernet token>`, using MultiFernet over `FIELD_ENCRYPTION_KEYS` (newest first; rotation via `rotate_str`).
  - Legacy plaintext is read transparently.
  - Applies to:
    - `SystemConfig.value`: all values; sensitive keys are also encrypted in the Redis cache.
    - `OrgWhatsAppConfig.access_token/app_secret/verify_token`
    - `OrgPaymentSettings.safepay_secret_key/bsecure_client_secret`
    - `OrgVoiceConfig.auth_token`
    - `User.cnic`
    - `Agent.cnic_number/national_id_number`
    - `DocumentScan.cnic_number/raw_ocr`
  - The webhook verify‑token lookup uses `verify_token_hash` (HMAC‑SHA256 blind index) plus a constant‑time compare.
  - Dev/test derive a key from `SECRET_KEY`. Production without a key fails the system check (`core.E002`).
  - Migrations widen the columns to text, then encrypt in place; reverse decrypts (the recovery path). Rehearsed forward/reverse on Postgres 15.
- **Consequences:** Equality filters on these columns no longer work (none existed except the verify token). Key loss is unrecoverable (KI‑028).
- **Rejected:** A new dependency such as django‑cryptography (`cryptography` is already present); pgcrypto (puts keys in SQL).

## ADR‑009 — Freeze pattern for out‑of‑scope modules
- **Date:** 2026-11-21
- **Decision:** `apps/core/permissions.FeatureEnabled(flag, methods=…)` returns 403 while the flag is off. Frozen modules block **new** activity only; history and the management of existing records stay available.
  - Voice: outbound blocked; inbound calls get a polite TwiML hang‑up after the signature check.
  - Syndication: create, syndicate, submissions/accept and browse blocked; withdraw, update and the ledger stay available.

## ADR‑010 — Compliance grounding context
- **Date:** 2026-11-21
- **Decision:**
  - The search tool registers the property in focus with `ComplianceEngine.set_active_property` only when exactly one own‑DB listing is returned.
  - `AIServiceManager.process` runs `check_output` after the output guard. Violations replace the reply with the human‑handoff text and are not cached.
- **Residual:** Replies about multiple listings are not grounded (M2 AITrace/tool runtime).

## ADR‑011 — OTP codes stored as keyed HMAC
- **Date:** 2026-11-21 (session 4)
- **Decision:** `OTPCode.code_hash = blind_index("otp:v1:{purpose}:{phone}:{code}")` (HMAC‑SHA256, key `FIELD_HASH_KEY` or derived from the field‑encryption key). The plaintext lives only on the in‑memory instance returned by `OTPService.issue()` for delivery. Verification uses `hmac.compare_digest`.
- **Why keyed HMAC, not bcrypt/PBKDF2:** a 6‑digit space is brute‑forced offline in milliseconds whatever the hash cost; only a server‑side key outside the DB protects it. HMAC also keeps verification cheap.
- **Consequences:** changing `FIELD_HASH_KEY` invalidates outstanding OTPs (5‑minute life, acceptable). Migration `users 0013` hashes existing rows; reverse cannot recover codes and marks unused ones used.
- **Rejected:** Fernet‑encrypting the code (reversible, no benefit); Django `make_password` (slow and gives no protection for 10⁶ codes).

## ADR‑012 — Inbound WhatsApp retry policy under DB failure
- **Date:** 2026-11-21 (session 4)
- **Decision:**
  - `process_incoming_whatsapp_task` retries only on `OperationalError`/`InterfaceError`, with backoff 5→160 s (6 retries, ≈5 min). The message waits in the Redis broker, not the DB.
  - **Retry only if no user‑visible side effect happened in the failed attempt.** A contextvar counter (`inbound_resilience.track_side_effects`) is incremented by `WhatsAppClient.send_text/send_template/send_document` and by the agent‑room broadcast. Duplicate replies are worse than one lost turn after a reply.
  - Org lookup DB errors retry instead of falling back to `org=None`, which would have answered with global credentials.
  - Exhausted retries → Redis list `wa:inbound:deadletter` (capped 10 k); `manage.py replay_whatsapp_dead_letters [--count|--limit N]`.
  - `acks_late` + `reject_on_worker_lost`: a killed worker re‑queues the message (at‑least‑once).
  - The webhook releases its idempotency key when `.delay()` fails and returns 500, so Meta's re‑delivery is processed.
- **Rejected:** persisting inbound messages to a DB inbox table first (the DB is what fails in this scenario); retrying on any exception (non‑transient bugs would loop and could repeat LLM cost).
- **Follow‑up:** T‑1021 transactional outbox for outbound side effects.

## ADR‑013 — `OrganizationMembership` is the canonical user ↔ org link; legacy links are projections
- **Date:** 2026-11-21 (session 5) · Task T‑1030 · KI‑010
- **Context (inventory):**

  | Link | Shape | Writers | Readers |
  |---|---|---|---|
  | `OrganizationMembership` (user, org, role, employment_type, is_active) | many‑to‑many, 8 roles | org registration, `/organizations/me/members/` invite/remove, migration 0010 | `get_user_org()` (flag `use_membership_rbac`, default on, ~17 modules), the `IsOrg*` role permissions, WhatsApp WS consumers + JWT middleware |
  | `Organization.admin_user` | one‑to‑one | org registration, admin org create/PATCH | `owned_organization` in ~19 modules (tenant middleware, `IsOrgAdmin`, `IsOrgMember`, billing, compliance, verification, inventory…) |
  | `Agent.organization` (+ `Agent.user`) | FK, "primary org" | agent registration, developer agent create, `/agents/team/` add/remove, admin | tenant middleware, `IsOrgMember`, agent/lead querysets, lead routing |
  | `AgentOrgMembership` (agent, org, commission, `can_receive_leads`…) | collaboration terms for freelancers | **none in code** (admin only) | `leads.services.suggest_agents_for_lead` |
  | `AgentOrganizationMembership` (FreelanceAgentProfile, org, commission) | duplicate of the above | none | none (tests only) |

- **Problem:** The writers of `Agent.organization` and `admin_user` never wrote `OrganizationMembership`, yet `get_user_org()` reads it by default. Consequences found:
  - agents registered or added to a team after migration 0010 resolved to **no org**;
  - an agent **removed** from a team kept an active membership, so `get_user_org()` and the WS consumers still granted org access;
  - an org created by a platform admin left its developer without an org;
  - removing a member via the members API left `Agent.organization` set, so the legacy readers kept access.
- **Options:** (a) make `Agent.organization`/`admin_user` canonical and drop the table; (b) make the table canonical and keep the columns as projections maintained by one write path; (c) big‑bang rewrite of all ~35 readers now.
- **Decision:** (b), contracted in phases:
  - **Phase A (done, session 5):**
    - `apps/organizations/membership.py` is the single write path. `post_save` hooks on `Agent` and `Organization` (snapshot taken at `post_init`, `update_fields`‑aware) mirror every projection change:
      - agent joins → `agent`/`freelance_agent` membership ensured (reactivated if inactive);
      - agent leaves or is unlinked → its membership is deactivated, **except owner**;
      - `admin_user` changes → the new admin gets owner (created or promoted) and the previous admin's membership is deactivated, matching legacy `owned_organization` semantics.
    - The members‑API delete goes through `remove_member()`, which also clears `Agent.organization` and decrements the seat ledger.
    - `primary_membership()` makes `get_user_org()` deterministic. Preference order: owned org, then `Agent.organization`, then earliest joined. It used to be an unordered `.first()`.
    - Migration `organizations 0024` backfills missing rows (create‑only, idempotent).
    - `manage.py reconcile_memberships [--apply] [--fail-on-drift]` reports drift. It repairs only missing owner and agent rows.
  - **Phase B (T‑1031):** move every `owned_organization`/`agent_profile.organization` reader (tenant middleware, `IsOrgAdmin`, `IsOrgMember`, the ~19 modules) to membership‑based policies with an explicit active‑org context, then remove the `use_membership_rbac` legacy branch. Also restrict which roles may grant `owner`/`org_admin` through the invite API, which today accepts any role.
  - **Phase C (contract, after B, `reconcile` clean in prod):**
    - Drop `AgentOrganizationMembership`, which has no readers.
    - Re‑key `AgentOrgMembership` as collaboration terms on a membership.
    - Replace `Organization.admin_user` one‑to‑one by the owner membership, so one user can own several orgs.
    - Keep `Agent.organization` only as a primary‑org pointer, or drop it.
- **Why not (a):** one FK cannot express multi‑org freelancers, roles or history; the role permissions and WS auth already use the table. **Why not (c):** too large to verify in one step; phase A makes both read paths agree first, so phase B can switch readers one by one with no behaviour change.
- **Consequences:**
  - Creating an `Agent` with an org, or an `Organization` with `admin_user`, now creates the membership automatically. Code or tests that `create()` the same row afterwards must use `get_or_create`; three test files were updated.
  - Bulk `QuerySet.update()` of these columns bypasses the hooks. No such code exists; `reconcile_memberships` detects it.
  - A pending (self‑registered) agent gets an active membership, the same access the legacy path gave. Their user is inactive until approval.
  - Report‑only categories (`extra_owner`, `orphan_agent`, `collaboration_without_membership`) need a human decision and are never auto‑revoked.
- **Rejected:** Deactivating "orphan" agent memberships automatically. They can be legitimate invites to a second org.

## ADR‑014 — Transactional outbox for outbound side effects
- **Date:** 2026-11-21 (session 5) · Task T‑1021 · GAP‑043, TR‑09, KI‑029
- **Context:** Outbound WhatsApp sends were queued with `.delay()` beside the DB write (dual write). A broker outage after commit lost the message; a `.delay()` before commit could run before the row existed. OTP tasks also carried the plaintext code in the Redis broker (KI‑029). While tracing these paths, KI‑033 was found: since commit `8cee1dd`, 12 call sites called `WhatsAppClient` instance methods on the class. They raised `TypeError`, which was swallowed, so **no OTP, notification, hot‑lead alert, verification, listing‑alert or deal message was ever delivered**. Tests hid it by patching the method at class level.
- **Decision:**
  - **Model:** `apps.core.models.OutboxMessage`. Fields:
    - `kind`, `idempotency_key` (unique);
    - `payload` (JSON in an `EncryptedTextField`, ADR‑008);
    - `status` (pending/sending/sent/failed/expired), `attempts`/`max_attempts`;
    - `available_at`, lease `locked_until`, `expires_at`, `last_error`, `provider_ref`.
  - **Enqueue:** `apps.core.outbox.enqueue(kind, payload, key=…)` writes the row in the caller's transaction and queues `dispatch_outbox_message(id)` **on commit**. The broker carries only the id. The same key twice returns the existing row.
  - **Dispatch:** claiming is an atomic conditional `UPDATE` of a due row → `sending` with a 2‑minute lease and `attempts+1`. Outcomes:
    - handler success → `sent`;
    - `PermanentDeliveryError` → `failed`;
    - any other error → exponential backoff (15 s doubling, capped at 15 min) until `max_attempts`, then `failed`;
    - past `expires_at` → `expired`, not sent.
  - **Relay:** `relay_outbox` (beat, every 30 s) re‑queues due rows and expired leases, covering a lost on‑commit enqueue or a dead worker. `purge_outbox` (daily) deletes sent/expired rows after 7 days and failed rows after 30.
  - **Handlers:** an explicit registry `outbox.HANDLERS` maps kind → dotted path:
    - `whatsapp.otp` (expires with the OTP; payload wiped once terminal);
    - `notification.whatsapp` (idempotent on `Notification.status`; marks the notification failed on a closed 24 h window or on the final attempt).
  - **Queue:** the `OUTBOX_CELERY_QUEUE` setting defaults to `default`, because deployed workers are pinned with `CELERY_QUEUES`. The prod/dev default workers now also consume `outbound`, so operators can switch to a dedicated queue with one env change.
  - **KI‑033:** every call site now uses `get_wa_client(org)` with the relevant tenant (lead/property/deal org, otherwise global). A static test blocks class‑level calls.
- **Semantics:** at‑least‑once. A crash after Meta accepts a message but before the row is marked sent gives one duplicate after the lease expires (Meta has no idempotency key). The circuit‑breaker fallback (`MetaLocalBuffer`) returns `{}` and counts as sent. That buffer has its own replay path; unchanged.
- **Adopted now:** OTP delivery (all 4 issue sites) and `notify_user` / `retry_failed_notifications`. `send_otp_async` and `send_whatsapp_async` stay as shims for messages already in the broker at deploy time, and no longer log codes.
- **Not yet adopted (T‑1022):**
  - signal‑time sends (hot‑lead alert, verification status), listing alerts, escrow notices, the agent `send-message` action;
  - replies sent during inbound processing, which stay synchronous and are protected by ADR‑012.
- **Rejected:**
  - a new app or library (django‑outbox etc.): `apps/core` already hosts cross‑cutting models;
  - `select_for_update(skip_locked)` claiming: a conditional `UPDATE` gives the same exclusivity without holding a transaction open across the HTTP call;
  - routing to a new `outbound` queue by default: workers pinned to `default` would never deliver OTPs.

## ADR‑015 — Listing search indexes and index‑backed full‑text search
- **Date:** 2026-11-21 (session 6) · Task T‑1100 · GAP‑040
- **Context:** `properties` had only single‑column B‑trees, and none matched the SQL the code emits:
  - `city__icontains` / `location__icontains` (search service, recommendations, discovery engine) and `city__iexact` (API, `similar_to`) compile to `UPPER(col::text) LIKE/= UPPER(%s)`, which a plain B‑tree on `city` cannot serve;
  - `?search=` built `SearchVector(...)` with no config, i.e. the server's mutable `default_text_search_config`, which cannot be indexed, and filtered on `rank >= 0.01`, so every request computed `to_tsvector` for every active row.
- **Decision:**
  - `properties 0021`: `CREATE EXTENSION IF NOT EXISTS pg_trgm` (reverse keeps it). `django.contrib.postgres` added to `INSTALLED_APPS`; without it Django renders `OpClass` index SQL incorrectly.
  - `properties 0022` (`atomic = False`, `AddIndexConcurrently`):
    - `prop_fts_gin`: GIN on `to_tsvector('simple', title‖city‖location‖description‖ref_no)`;
    - `prop_city_trgm`, `prop_location_trgm`: GIN `gin_trgm_ops` on `UPPER(col)`, which serves both `LIKE '%x%'` and `=` (Postgres ≥ 14);
    - `prop_active_rank_idx`: B‑tree `(ai_score DESC, created_at DESC) WHERE is_active`, for the consumer search's ordered `LIMIT 10`. Not partial on `is_sandbox`, because `visible_to(org)` ORs the org's own sandbox rows.
  - `apps/properties/text_search.py` is the only full‑text path. `search_vector()` is used by both the index definition and the query, so they cannot drift. `apply_text_search()` filters with `@@` (index) and then ranks; ties go to newest. Tests pin each index to the real query shape with `enable_seqscan = off`.
  - **Query semantics:** letters/digits only (no tsquery operator from user input can reach `to_tsquery`), up to 8 tokens AND‑ed, tokens of ≥ 2 characters matched as prefixes, input capped at 200 characters. A full generated reference number goes to the unique `ref_no` B‑tree as an exact match, because the default parser reads `-000000001` as a signed integer.
- **Why `simple`:** an index needs an explicit config. Listings are multilingual place names that English stemming mangles; prefix matching recovers plural/suffix matches. Changing the config later means rebuilding `prop_fts_gin`.
- **Measured** (200k synthetic rows, Postgres 15): no sequential scan in any hot query; city/location/rank queries 0.3–5 ms; full‑text with a selective term 631 → 3 ms, with a term matching ~17 % of rows 757 → 238 ms (ranking reads every match).
- **Rejected:**
  - a stored generated `tsvector` column: it rewrites the table under an exclusive lock, and Django's `GeneratedField` would need a schema change for every listing‑v2 field (T‑1070). Reconsider with listing v2 if common‑term ranking becomes hot (KI‑035).
  - dropping the old single‑column indexes now: not measured in production; revisit with real `pg_stat_user_indexes` data.
  - GiST/PostGIS geo index: belongs to T‑1060.

## ADR‑016 — OpenAPI 3.1 via drf‑spectacular; staff‑only serving; budget ratchet
- **Date:** 2026-11-21 (session 6) · Task T‑1090a · GAP‑030, PRD D6, TR‑07
- **Context:** No schema existed, so API contracts were implicit and the separate frontend repo (R‑36) had nothing to build mocks from. The GAP analysis marks the package choice as an implementation decision.
- **Decision:**
  - `drf-spectacular==0.29.0` (BSD, no service, no cost); `DEFAULT_SCHEMA_CLASS = AutoSchema`; `OAS_VERSION 3.1.0`; path prefix `/api/v1`; split request/response components.
  - `apps/core/openapi.py`: auth extensions document the real schemes (`jwtBearer`, `jwtCookie`, `orgApiKey`).
  - **Serving:** `GET /api/v1/schema/` (YAML, `?format=json`). `SchemaAccessPermission` allows staff and platform admins only, unless `OPENAPI_SCHEMA_PUBLIC=True`. The schema is a map of every endpoint, so publishing it is an operator choice per environment. No Swagger UI: it loads assets from a CDN, which the CSP would have to allow.
  - **Publishing:** CI runs `manage.py spectacular --validate` and uploads `openapi.yaml` as the `openapi-schema` artefact. That is the published contract.
  - **Ratchet:** `tests/test_openapi_schema.py` fails when the number of unique generator errors (154) or warnings (32) grows. The 154 are `APIView`s with no serializer (KI‑037, T‑1091). Budgets are lowered as views are annotated.
- **Error envelope (T‑1090b) direction:** additive, keeping DRF's current shapes for the separate frontend. *Superseded in detail by ADR‑017:* the key is `error_info`, not `error`, because ~50 views already return `error` as a string.
- **Rejected:**
  - `drf-yasg`: Swagger 2.0 only;
  - a hand‑written schema: drifts from the code;
  - a public schema by default;
  - failing CI on any generator warning: it would block every PR until all 154 views are annotated.

## ADR‑017 — Request id and additive `error_info` envelope
- **Date:** 2026-11-21 (session 6) · Task T‑1090b · GAP‑031, PRD D6
- **Context:**
  - Error bodies came in at least four shapes: DRF `{"detail"}`, field‑keyed validation dicts, hand‑built `{"error": "<string>"}` (~50 views), and middleware `JsonResponse`s. Unrouted `/api/` URLs and unhandled exceptions returned Django HTML.
  - There was no request id, so support could not tie a user's error to a log line (only the security middleware read an inbound `X-Request-ID`).
  - The frontend is a separate repo, so existing keys must not change.
- **Decision:**
  - **`RequestIdMiddleware`** (first app middleware):
    - keeps a well‑formed inbound `X-Request-ID` (`[A-Za-z0-9._-]{8,64}`), otherwise generates a UUID hex, so no log injection is possible;
    - sets `request.request_id` and `META`, plus a context var that `TraceContextFilter` adds to every log line (`request_id` in the JSON format);
    - echoes the id in the response header. CORS allows and exposes the header.
  - **Envelope:** every JSON error under `/api/` gains `error_info: {status, code, message, fields?}` and `request_id`. Nothing else in the body changes. Two layers fill it:
    - `security_exception_handler` → `attach_envelope` for DRF exceptions: precise codes (`not_authenticated`, `throttled`, custom `code=` values) and `validation_error` with `fields`;
    - `ErrorEnvelopeMiddleware` (right after the request‑id middleware) for every other JSON error: hand‑built responses (their string `error` is kept as `message`), security/PII/tenant middleware rejections, and the new `handler404`/`handler500`, which return JSON for `/api/` paths and never leak exception text.
  - It is documented as the `ErrorEnvelope` schema component.
- **Why `error_info` and not `error`:** clients that do `data.error || data.detail` would render an object where they render a string today.
- **Limits:**
  - non‑object bodies (a bare list) are not wrapped; the header still carries the id;
  - the middleware skips streaming and > 256 KB bodies;
  - hand‑built responses get only status‑derived codes until they raise DRF exceptions (KI‑038).
- **Rejected:**
  - replacing bodies with an RFC 7807 problem document: breaking for the frontend;
  - envelope in the exception handler only: misses ~50 hand‑built responses and every middleware rejection;
  - trusting any inbound request id.

## ADR‑018 — Opt‑in `Idempotency-Key` for create POSTs
- **Date:** 2026-11-21 (session 6) · Task T‑1090d · GAP‑032, PRD D6
- **Context:**
  - A client that times out on "book a viewing", "start checkout" or "create enquiry" cannot tell whether it worked; a retry creates a second appointment or checkout session.
  - Only payment webhooks had idempotency (`billing` event table).
  - Requests run in autocommit (`ATOMIC_REQUESTS` is off).
- **Decision:**
  - **Model:** `core.IdempotencyRecord`:
    - unique `(scope, key)`, with `scope = user:<pk>`;
    - `request_hash` = sha256 of method, path and canonical JSON body;
    - `status` in_progress/completed and `response_status`;
    - `response_body` encrypted (ADR‑008), because it can hold lead details or payment references;
    - `locked_until` lease (5 min) and `expires_at` (`IDEMPOTENCY_TTL_HOURS`, default 24).
  - **Opt in per handler** with `@idempotent` (`apps/core/idempotency.py`), applied to:
    - lead create, appointment create;
    - `payments/checkout/<deal>/`, `payments/sepa/<deal>/`;
    - `deals/lock/` (the feature itself is frozen).
  - **Semantics:**
    - no header → unchanged;
    - first use → the claim row is committed, then the view runs;
    - a 2xx is stored; any error or exception deletes the row, so the client can correct the request and retry with the same key;
    - same key + same request after completion → stored status/body replayed with `Idempotent-Replayed: true`;
    - same key + different request → 422 `idempotency_key_reused`;
    - still running → 409 `idempotency_request_in_progress`, unless the lease is stale (crashed worker), in which case the new request takes over;
    - expired → the key is free again.
  - The key must be 1–255 printable ASCII characters. Anonymous requests are not tracked; every target needs authentication.
  - **Ops:** hourly beat `purge-idempotency-records`. CORS allows `Idempotency-Key` and exposes `Idempotent-Replayed`. The header is documented in OpenAPI for each target.
- **Why opt‑in:** a global middleware would cache GET‑like POSTs (AI chat, search) and webhook bodies. Per‑handler wrapping runs after DRF's auth, permission and throttle checks.
- **Limits:**
  - at‑most‑once only among requests that carry the same key; clients must send one;
  - the stale‑lease takeover can run the view twice if the first request is merely slow (more than 5 min);
  - multipart bodies hash file names, not contents.
- **Rejected:**
  - Redis‑only storage: lost on eviction/restart, which is exactly the retry window that matters;
  - storing 4xx outcomes: a failed request has no side effect to protect, and storing it would block a corrected retry.

## ADR‑019 — `Market` entity; market‑driven defaults; expand step for `market` links
- **Date:** 2026-11-21 (session 7) · Task T‑1050a · TR‑04, GAP‑014
- **Context:**
  - Markets existed only as a code dataclass (`apps/markets/registry.py`: PK, AE, GB, US, CA) plus scattered literals: 11 model defaults (`'PKR'`, `'PK'`, `'marla'`/`AreaUnit.MARLA`), about 34 `'PKR'` literals in logic, about 20 `or 'PK'` fallbacks, and `TIME_ZONE='Asia/Karachi'`.
  - Every record silently became Pakistani. The M1 exit criterion asks for no hardcoded PK defaults outside the market pack, checked in CI.
- **Decision:**
  - **`markets.Market`** (primary key = ISO code):
    - status (active/beta/inactive);
    - currencies + default, area units + default, languages + default;
    - IANA time zone, price format, tax system, `settings` JSON;
    - `clean()` checks that each default is in its list and that the time zone exists.
  - **Seed:** `markets 0002` with values frozen in the migration (create‑only, so operator edits survive). PK is `active`, AE/GB/US `beta`, CA `inactive`. A test keeps the seed equal to the registry.
  - **Two read paths** (`apps/markets/services.py`):
    - **model defaults** `default_country/default_currency/default_area_unit()` read the code registry for `settings.DEFAULT_MARKET` (env, default `PK`). They never touch the DB, because Django evaluates defaults inside migrations, where a query against a missing table would abort the transaction. An unknown `DEFAULT_MARKET` raises.
    - **runtime** `get_market(code)` reads the row, caches it for 5 min (invalidated by `post_save`/`post_delete`) and falls back to the registry.
  - **Literal defaults replaced** by the callables in `Property`, `Organization`, `Lead`, `Payment`, `EscrowDeal`, `AuditBenchmark` and `PropertyAudit`. This changes migration state only; there is no SQL.
  - **Expand step:**
    - nullable `market` FK (`PROTECT`) on `Property` (`properties 0023/0024`) and `Organization` (`organizations 0025/0026`);
    - backfill from `UPPER(country)` where a market row exists;
    - new rows derive it on create; an explicit market is kept, and it is not recomputed on update;
    - `country` columns stay;
    - `manage.py market_backfill_report [--apply] [--max-unmatched-pct N]` reports the result.
  - **Admin:** editable, not deletable (retire a market with `status`).
  - **CI check (exit criterion):** `tests/test_markets.py::HardcodedMarketDefaultsTest`. Literal market defaults outside `apps/markets/` and migrations fail the build. The remaining logic literals are a ratchet: `'PKR'` ≤ 34, `or 'PK'` fallbacks ≤ 20, `'Asia/Karachi'` in `apps/` = 0.
- **Not in this step:**
  - moving the logic/formatting literals to the market pack (T‑1050b);
  - UTC + per‑entity time zone (T‑1050c). Switching `TIME_ZONE`/`CELERY_TIMEZONE` moves every crontab beat job and report bucket by 5 hours, so it ships on its own with a schedule review;
  - the contract step (making `market` required, retiring `country` as the source) after `market_backfill_report` is clean in prod.
- **Rejected:**
  - DB‑read defaults: they are unsafe in migrations and add a query per instantiation;
  - a `market` FK on every money model now: currency belongs to the listing, deal or payment, not to a market; revisit with listing v2 (T‑1070);
  - deleting the registry: code packs (calculators, parsers, document types) stay in code, and the registry is their index.
