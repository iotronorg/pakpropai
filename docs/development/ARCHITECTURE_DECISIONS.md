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

- **Addendum — T‑1022a (2026-11-22, session 8):** generic kind `whatsapp.text`.
  - `apps.notifications.delivery.enqueue_whatsapp_text(phone, text, key=…, organization=…, skip_window_check=…, expires_at=…)`. The org's WhatsApp credentials are resolved **at delivery** from `organization_id`.
  - A closed 24 h window is a permanent failure.
  - The kind is in `SENSITIVE_KINDS`: the text can contain client phone numbers, so the payload is wiped after delivery.
  - **Keys name the business event** (`hot_lead:<lead>:<last_scored_at>`, `verification:<id>:<status>`, `listing_alert:<property>:<lead>`, `appt_reminder:<appt>:<scheduled_at>`, `stale_reminder:<agent>:<date>`, `reengage:<lead>:<date>`). A rerun task or re‑fired signal therefore sends nothing new.
  - Batch tasks queue the message and set their `*_sent_at` marker in one transaction. The marker now means "queued", not "provider accepted".
  - Appointment reminders expire at the visit time.
  - Synchronous sends whose caller needs the provider result (lead `send-message`, chat replies) stay direct.
- **Addendum — T‑1022b (same session):**
  - Adopted for the escrow expiry notices (`deal_expired:<deal>:<buyer|seller|agent>`, in the same transaction as the status change), the seller deal‑lock code (`deal_lock_request:<deal>`), the audit companion text (`audit_companion:<audit>`) and the scraped‑listing follow‑up (`scrape_followup:<cache key>:<phone>`, expires after 30 min).
  - **Stay direct, by decision:**
    - campaigns and AI re‑engagement: they pace sends with `MetaTierRateLimiter` and retry Meta 429s at the task level, and campaigns keep per‑recipient delivery state. Outbox dispatch is not rate‑limited and would bypass that;
    - the audit PDF chain (media upload → document send → fallback link): it has its own `AuditDeliveryFailure` records and fallback.

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

- **Addendum — T‑1050b (2026-11-22, session 8):**
  - **Market‑neutral helpers** in `apps.markets.services`: `country_or_default`, `org_country`, `currency_for_country`, `format_price`. Use them for fallbacks and display.
  - **Code that is Pakistan‑only by design** imports `apps.markets.pk.{COUNTRY, CURRENCY, AREA_UNIT}` instead of literals: the audit engine, the WhatsApp audit/listing tool contract, PK payment gateways, `FinancialConfig` defaults and the NL search schema. The coupling stays explicit and greppable until those features get per‑market implementations (KI‑045).
  - **Money is labelled in the currency it is in**, never relabelled with the deployment default.
  - **The ratchet keeps 5 literals:** the multi‑currency alias table in `ai/compliance_filters.py`.

## ADR‑020 — Membership‑based permissions, active org context, grant policy (ADR‑013 phase B, part 1)
- **Date:** 2026-11-21 (session 7) · Task T‑1031a · TR‑06, GAP‑015, KI‑010, KI‑030
- **Context:**
  - After ADR‑013 phase A the membership table was correct, but the core permission classes still decided on `user.role` and the legacy links:
    - `IsAdminOrOrgAdmin` (34 uses) let in any `role='developer'`, and its object check passed only `Organization.admin_user`;
    - `IsOrgAdmin`/`IsOrgMember` read `owned_organization`/`agent_profile.organization`;
    - `get_user_org()` still had a legacy branch behind `use_membership_rbac`.
  - A user in two orgs had no way to choose one.
  - The members API accepted any role on invite (including `owner`), and an org admin could create or remove other org admins.
- **Decision:**
  - **Single source:** `get_active_membership(user)` → `get_user_org()`, `active_org_role()`. The legacy branch and the `use_membership_rbac` switch are removed; the config key is now unused.
  - **Active org context:**
    - `ActiveOrganizationMiddleware` copies `X-Organization-ID` into a context var (a UUID, otherwise "invalid");
    - `get_active_membership` uses it only if the user has an **active** membership there. Otherwise the result is `None` (deny), never the primary org;
    - no header → `primary_membership()` (ADR‑013);
    - CORS allows the header.
  - **Policies:**
    - `IsAdminOrOrgAdmin`: platform admin, or `owner`/`org_admin` of the active org. The object check passes when the user administers the object's org (org itself or `obj.organization_id`);
    - `IsOrgAdmin`: same, without platform admins;
    - `IsOrgMember`: any active role in the active org.
  - **Grant policy (members API):**
    - `owner` is never grantable by invite (transfer is a separate flow);
    - only the owner (or a platform admin) may grant or remove `org_admin`;
    - the reactivation path follows the same rules.
  - **Security engine:**
    - `X-Organization-ID` is removed from the pre‑auth "tenant override" list, because JWT users are still anonymous at that point, so the header could never be valid there;
    - a selector naming a foreign org is recorded as a `cross_tenant` `ApiSecurityEvent` (once per request) where it is checked;
    - `X-Tenant-ID`/`X-Org-ID`/`X-Workspace-ID` stay forbidden.
  - **KI‑030:** `/auth/otp/send/` accepts `purpose` ∈ {`otp_login` (default), `registration_verify`} only.
- **Behaviour changes:**
  - users with an `org_admin` membership but `user.role='agent'` now pass org‑admin checks;
  - a `developer` without an owner/org_admin membership no longer does (phase A backfill + `reconcile_memberships` make that set empty);
  - a DB row `use_membership_rbac=false` is now ignored.
- **Addendum — T‑1031b (same day):** all request‑time readers moved.
  - **`get_admin_org(user)`** returns the active org only when the user is its `owner`/`org_admin`. Management actions that legacy code tied to `owned_organization` use it, so selecting an org where you are only a viewer gives no management access.
  - **`get_user_org`** scopes member data, the seat‑limit check and the security engine's auth context.
  - **`primary_org(user)`** is for *subject* users in tasks/audit records and ignores the request selector.
  - **`RTBFStatusView`** keeps the legacy rule (subject must own the requester's org), now via membership.
  - The leads serializer fallback and the `use_membership_rbac` default are removed.
  - Only `organizations/membership.py` (`primary_membership` preference) and the model definition still touch the legacy links.
- **Was pending (T‑1031b):** ~55 direct legacy reads in ~20 modules (billing, verification, provisioning, inventory, agents, compliance, security‑middleware auth context, `config/models.py`…) still use `owned_organization`/`agent_profile.organization`. They are correct for single‑org users (projections are synced) but ignore the selector.
- **Rejected:**
  - a request attribute set in Django middleware: JWT authentication happens later, inside DRF;
  - falling back to the primary org on an invalid selector: it would silently act on the wrong tenant;
  - keeping the header in the threat list and authenticating JWT in the security middleware: it duplicates auth on every request.

## ADR‑021 — Membership contract: one service for invite, role change, removal and ownership transfer
- **Date:** 2026-11-22 (session 8) · Task T‑1032a · KI‑010, GAP‑015
- **Context:**
  - After ADR‑020 the grant policy lived in `OrgMembershipView`, so any other writer could bypass it.
  - There was no role change and no ownership transfer. The only way to move ownership was to edit `Organization.admin_user` in the admin. The projection hook then **deactivated** the previous owner entirely.
  - Membership changes were not audited.
- **Decision:**
  - **Service.** `apps/organizations/membership.py` holds the contract: `check_invite`/`invite_member`, `change_role`, `remove_member(actor=…)` and `transfer_ownership`. Views only parse input and call it. Each operation runs in one transaction together with its `AuditLog` row:
    - `membership.invited` / `membership.reactivated` (CREATE);
    - `membership.role_changed` (UPDATE);
    - `membership.removed` (DELETE);
    - `organization.ownership_transferred` (UPDATE on the org).

    `before`/`after` hold ids, role, employment type and active flag only, with no phone numbers.
  - **Errors** are DRF exceptions with specific codes: `membership_invalid` (400), `membership_forbidden` (403), `membership_conflict` (409). The ADR‑017 envelope carries them.
  - **Policy** (ADR‑020, now enforced in the service):
    - actors must be `owner`/`org_admin` of the org, or a platform admin;
    - `owner` is never granted by invite or role change;
    - granting, changing or removing `org_admin` is owner‑only;
    - the owner's membership cannot be changed or removed (transfer first).
  - **Transfer rules:**
    - caller must be the current owner or a platform admin;
    - the target must be an **active `org_admin`** of the org (a two‑step promote → transfer);
    - the target must have **account type `developer`**, be active, and own no other org, because `admin_user` is one‑to‑one until ADR‑013 phase C;
    - previous owner(s) are demoted to `org_admin` and stay in the org;
    - `admin_user` is written with `QuerySet.update` so the legacy projection hook does not deactivate the previous owner. The caller's instance and its post‑init snapshot are updated so a later `save()` cannot revert it.
  - **API:**
    - `PATCH /organizations/me/members/{id}/` (role, employment type);
    - `POST /organizations/me/ownership/transfer/` (active org via `get_admin_org`);
    - `POST /organizations/{id}/ownership/transfer/` (platform admin).

    The members view is split into list and detail views (fixes 4 operationId collisions), and the members/ownership endpoints are documented in OpenAPI.
- **Why the account‑type rule:** about 10 readers still scope data on `user.role == 'developer'` (leads, agents, org list, tenant middleware). An `agent`/`client` account made owner would administer the org but see an agent's or no data. Changing `user.role` silently would break that user's agent views. Lift the rule when those readers move to membership roles.
- **Not done (deferred):**
  - **Invite acceptance:** an invite still adds an existing user immediately, without their consent. A pending/accept state is a product decision (open question).
  - **Phase C schema contraction:** T‑1032b, gated on a clean `reconcile_memberships` in prod.
- **Rejected:**
  - keeping the policy in views: the service is the only place every writer passes;
  - letting the projection hook handle transfers: it would remove the previous owner from the org;
  - auto‑promoting the target to `org_admin` during transfer: it skips a deliberate step for an irreversible‑feeling action.

## ADR‑022 — UTC for storage and scheduling; market‑local time for people and buckets
- **Date:** 2026-11-22 (session 8) · Task T‑1050c · TR‑04, GAP‑091
- **Context:**
  - `TIME_ZONE` and `CELERY_TIMEZONE` were `Asia/Karachi`, so every day/week/month bucket, every crontab and the admin were in PKT. A global product needs per‑market local time.
  - `strftime` on stored datetimes already produced UTC in messages (KI‑047), because `USE_TZ=True` returns UTC values.
- **Decision:**
  - **UTC everywhere internally:** `TIME_ZONE = CELERY_TIMEZONE = 'UTC'`.
  - **The three crontabs keep their former instants:** re‑engagement at 01/07/13/19 UTC (= 00/06/12/18 PKT); AML sync Sunday 21:00 UTC (= Monday 02:00 PKT). Interval schedules are unaffected.
  - **Monthly reports** run hourly on UTC day 1. Each org gets its **last complete month in its own zone**; the run is idempotent per (org, month) and catches up missed months. Pakistan reports appear from 05:05 PKT, previously 06:00. New York reports appear once New York reaches the 1st.
  - **Local time comes from the market:** `apps.markets.services.market_timezone(code)` → `org_timezone(org)` (market, else country, else the default market) → `user_timezone(user)` (active org; platform admins get the default market). An invalid zone falls back to UTC with a warning.
  - **Presentation:** `format_local(dt, org, fmt)` for anything a person reads.
  - **Bucketing:** Trunc* with `tzinfo=` and `local_day_bounds(tz)` ranges, instead of `__date` (which depends on the server zone).
- **Not converted (acceptable in UTC):**
  - admin‑only filters (`compliance` `date_from`);
  - internal ISO stamps (`date.today()` in metrics/ML, which uses the OS clock);
  - LLM context dates;
  - the audit PDF "generated" date.
- **Consequences:**
  - **django‑celery‑beat** is the scheduler (`DatabaseScheduler`). Settings entries are re‑synced into new UTC `CrontabSchedule` rows when beat starts. Admin‑created `PeriodicTask` rows keep their stored zone and still fire at the same instants, but should be reviewed.
  - **Django admin** shows UTC.
- **Rejected:**
  - `timezone.activate()` per request: JWT auth happens inside DRF, so a middleware cannot know the org, and implicit global state is harder to test than explicit `tzinfo`;
  - a single "06:00 UTC" monthly run: it would cut western markets' months early or deliver PK reports at 11:00 PKT.

## ADR‑023 — Listing schema v2: evolve `Property` in place, expand/contract in subtasks
- **Date:** 2026-11-22 (session 8) · Task T‑1070 · FR‑LST‑001/002/004, GAP‑010/012/045, KI‑035
- **Context:**
  - `Property` has no purpose (sale/rent), rooms, floors, year built or price terms.
  - The size field is misnamed (`area_marla` holds a value in `area_unit`).
  - Price is a single integer in major units.
  - Readers: about 17 app files touch `area_marla`, and about 40 touch `price`. Consumers include the AI tools, search, scrapers, audit, scoring, the partner API and syndication.
- **Decision:**
  - **Evolve `Property` in place.** The table stays `properties`; no new `Listing` model. "Listing" is the API vocabulary only.
  - **Every step is expand → migrate readers → contract.** Each subtask is releasable on its own.
  - **Expand migrations are rolling‑deploy safe:** new columns are nullable, or NOT NULL with a constant `db_default` (Django 5.2). Pre‑v2 code can still insert rows, and Postgres 11+ adds the column without a table rewrite. Indexes are added `CONCURRENTLY` in their own non‑atomic migration (pattern from 0022).
  - **Subtasks:**

    | Subtask | Scope | Status |
    |---|---|---|
    | T‑1070a | Expand core fields and price terms (`purpose`, `bedrooms`, `bathrooms`, `floor`, `total_floors`, `year_built`, `parking_spaces`, `pets_allowed`, `price_type`, `price_negotiable`, `price_on_request`, `deposit`, `service_charge`, `attributes`). Wire them through the API, search and the AI search tool. Existing rows become `purpose='sale'`: scrapers fetch `/sale/` only and the seed data is all sale | done |
    | T‑1070b | Size rename: `size_value`/`size_unit`/`size_m2` and a nullable `plot_size_m2`. Dual‑write with `area_marla`/`area_unit` via an instance snapshot; migrate readers; the API keeps `area_marla` as a deprecated alias | done |
    | T‑1070c | `PriceHistory` table, recorded on every price change (FR‑LST‑002, P1) | done |
    | T‑1070d | Category/subtype taxonomy (adds land and hospitality) plus `Amenity`/`ListingAmenity` | done |
    | T‑1070e | Market attribute schema: `Market.listing_schema` validates `attributes` (FR‑LST‑004) | done |
    | T‑1070f | Stored `tsvector` column + trigger (KI‑035) | planned |
    | T‑1070g | **Contract:** drop `area_marla`/`area_unit` and decide minor‑unit money. **Destructive; needs owner approval after one release with no old readers** | planned |
  - **Cross‑field rules live in `Property.v2_errors()`,** shared by `Model.clean` and the API serializers. A partial update is validated against the stored values. The rules:
    - a sale price cannot be per month or per year;
    - `floor ≤ total_floors`;
    - `year_built` is between 1800 and now + 10 years;
    - `deposit` and `service_charge` are not negative;
    - `attributes` is an object with at most 50 keys and 8 KB.
  - **Search purpose semantics:**
    - `buy`/`sale` → `sale`;
    - `rent` → `rent_long` + `rent_short`;
    - `lease` → `lease_commercial`;
    - an unknown purpose → no filter;
    - a rent or lease search skips the scrapers (they are sale‑only) and promises no follow‑up.
- **Money stays in major units** (`BigInteger`, ISO currency) until T‑1070g. PKR, AED and GBP listing prices never use sub‑units in practice. The conversion touches payments and escrow, so it belongs in the contract step.
- **Rejected:**
  - a new `Listing` table with a copy‑over: it duplicates every FK (leads, deals, audits, syndication) and needs a big‑bang cutover;
  - Django `RenameField` for `area_marla`: old code breaks during a rolling deploy;
  - a free JSON blob for the core fields: they are not indexable or filterable, and not typed in OpenAPI.

### ADR‑023 addendum — T‑1070b size dual‑write (2026-11-22)
- **Snapshot:** `Property.from_db` records the loaded (v1, v2) size pairs.
- **On `save()`:**
  - the v2 pair wins if it changed since load (or was set on a new object);
  - otherwise the v1 pair wins, which covers pre‑v2 writers and un‑backfilled rows;
  - a load with deferred size fields counts as "v1 wins";
  - `size_m2` and `area_sqm` are both recomputed;
  - `update_fields` that touch any size field are extended to cover all six.
- **Bypass writes:** `QuerySet.update` and raw SQL skip the sync. No app code writes size that way. Old pods during a rollout write v1 only, so `manage.py sync_listing_size` (batched v1 → v2 where they differ) is run once after rollout.
- **Readers:**
  - match and score on the **canonical m²** (`size_in('marla')` for the marla‑based PK benchmarks);
  - display the **listed unit** (`size_display()`);
  - the AI/scraper `area_marla` parameters still mean "marla" (user intent in the PK market) and are converted at the DB boundary.

### ADR‑023 addendum — T‑1070c price history (2026-11-22)
- **Storage:** an append‑only `property_price_history` table. It snapshots `price`, `currency`, `price_type` and `price_on_request`.
- **Writes happen inside `Property.save()`,** in the same transaction as the listing (both commit or roll back together):
  - on create, when the listing is priced or "on request";
  - on update, when the terms differ from the loaded snapshot;
  - a deferred load is compared with the latest row instead;
  - `update_fields` without any price field skip recording;
  - the snapshot refreshes only for fields actually written, a bug the tests caught.
- **Attribution is explicit:** callers wrap writes in `price_change_context(user, source)` (`api`, `admin`, `ai_tool`). Otherwise the row gets `system` and no user. A contextvar keeps the model free of request coupling.
- **API:** `GET /properties/{id}/price-history/` uses `get_object()`, so it has exactly the listing's visibility. It is paginated, newest first, and **does not expose `changed_by`**. The actor is personal data, kept for audit and admin only.
- **Gaps:**
  - writes from pre‑v2 pods during a rollout and `QuerySet.update(price=…)` are not recorded (no app code does the latter);
  - the baseline row is dated at listing creation, not at the last real change.

### ADR‑023 addendum — T‑1070d taxonomy and amenities (2026-11-23)
- **Category is canonical; `property_type` is a deprecated alias** kept in sync by `Property.save()` until T‑1070g.
  - `property_type → category`: `plot → land`, others unchanged.
  - `category → property_type`: `land → plot`, `hospitality → commercial`, others unchanged.
  - `hospitality` has no v1 value. A row with `category=hospitality, property_type=commercial` counts as consistent, so it is never "repaired" back to commercial.
- **Sync rule** (same snapshot pattern as size, T‑1070b): a category changed since load (or set on a new object) wins and projects to `property_type`. Otherwise `property_type` wins: pre‑v2 writers, un‑backfilled rows and deferred loads. A subtype that no longer fits the resulting category is cleared. `update_fields` touching any of the three fields is extended to all three.
- **Subtypes** are one flat, market‑neutral list per category in `apps/properties/taxonomy.py` (code, not a table). They change rarely, and code keeps them typed in OpenAPI (`enum`). Market‑specific ones such as PK `upper_portion` and `plot_file` are harmless elsewhere. Validation is in `Property.v2_errors()`, shared by `clean()` and the serializers.
- **API rules:**
  - `property_type` alone derives `category`;
  - `property_type` and `category` that disagree → 400 (like `area_marla` vs `size_value`);
  - an explicitly sent subtype that does not fit the category → 400;
  - a stored subtype made invalid by a category change is cleared silently.
- **Amenities are a table** (`amenities`: `key`, `group`, `labels` per language, `markets` list, `is_active`, `sort_order`) because operators edit it in admin. Migration 0033 seeds 35 entries with `en` + `ur` labels. `markets` empty = every market. Some are PK‑only (servant quarters, drawing room, gas connection) and one is AE‑only (maid's room).
  - `property_amenities` (`ListingAmenity`) is the M2M through table. `PROTECT` on the amenity: retire one with `is_active=False`, not by deleting it.
  - The API reads and writes amenities as a list of keys. Inactive or unknown keys → 400. Keys must be available in the listing's market. At most 60 per listing.
- **Filtering:** `?amenities=a,b` means all must match, via one grouped subquery (`COUNT(DISTINCT amenity) = n`) rather than n joins. At most 10 keys; unknown keys → 400. `?category=`/`?subtype=` filters; `?type=` stays as a deprecated alias.
- **Search service and AI tool:** amenities are a hard filter. Unknown keys are dropped rather than rejected, following the AI spec (unknown amenities become soft notes). Scrapers carry no amenity data, so an amenity search skips them and promises no follow‑up. `PropertyResult.amenities` carries the listing's keys for the response and for later grounding (GAP‑054).
- **New endpoint:** `GET /properties/taxonomy/?market=&lang=` lists categories with subtypes and the active amenities for a market, with labels in the requested language (falls back to `en`).
- **Rollout:** 0031 adds nullable columns and new tables; 0032 is a batched backfill; 0033 is the seed; 0034 builds the `(category, is_active)` index concurrently. Pre‑v2 pods write only `property_type`, so `manage.py sync_listing_taxonomy` repairs drift after rollout.
- **Rejected:**
  - renaming `property_type` in place: old pods break during a rolling deploy;
  - an amenity JSON array on `Property`: no referential integrity, and no per‑market labels or admin curation;
  - a subtype table: no current need for operator edits, and it loses the OpenAPI enum.

### ADR‑023 addendum — T‑1070e market attribute schema (2026-11-23)
- **Format:** `Market.listing_schema` uses a small in‑house format, defined and validated in `apps/markets/listing_schema.py`. It is not JSON Schema.
  - `{"fields": {key: {"type": string|integer|number|boolean|enum, …options}}, "additional": "reject"|"allow"}`
  - Options: `label`; `max_length` and `pattern` for strings; `min` and `max` for numbers; `values` for enums.
  - `{}` means no market rules.
  - Why not JSON Schema: operators edit it in admin, the same definition will drive listing forms, and the small vocabulary keeps validation predictable. It also needs no new dependency.
- **Validation:**
  - `Market.clean()` rejects a malformed schema.
  - Listing values are checked in `Property.clean()` and in the API serializers. `null` means "unknown" and is allowed for a declared key.
  - Strings are length‑checked before the regex runs. Patterns are staff‑authored only.
- **When attributes are checked (API):** only when `attributes` or `country` is written. Stored legacy attributes do not block an unrelated PATCH. A country change re‑checks the stored attributes against the new market.
- **Seeds (markets 0004):** only empty schemas are filled, so admin edits survive. All seeded schemas use `additional: reject`.
  - PK: possession, corner, park facing, main road, plot number, block, society approval.
  - AE: RERA/Trakheesi permit, broker ORN, freehold, chiller free, rent cheques.
  - GB: EPC rating, tenure, lease years remaining, council tax band, ground rent.
  - US/CA: `{}`.
- **Cache:** `MarketInfo` gained `listing_schema`. The cache prefix moved `market:v1:` → `market:v2:`, so old cached entries are never read.

### ADR‑015 addendum — T‑1070f stored search document (2026-11-23)
- **Decision:** `properties.search_tsv` (tsvector) is maintained by a `BEFORE INSERT OR UPDATE OF title, city, location, description, ref_no, search_tsv` trigger. `apply_text_search` filters and ranks on the column through `prop_search_tsv_gin`. The config and fields are unchanged (`simple`; title/city/location/description/ref_no), and results and order are tested equal to the legacy expression query.
- **Why a trigger and not `save()`:** every writer stays correct without app changes: pre‑v2 pods during a rollout, `QuerySet.update`, raw SQL and admin bulk actions. The trigger also fires when `search_tsv` itself is written, so a stale value written back by a full model `save()` is recomputed, and nothing can store a wrong document.
- **Why not a Postgres generated column:** adding one rewrites the whole table. A nullable column plus a batched backfill does not.
- **Measured** (200k synthetic rows, 119k–169k matches): 828 → 124 ms, 2020 → 134 ms, 1741 → 147 ms. Ranking no longer recomputes `to_tsvector` per match (KI‑035).
- **Contract:** the legacy expression index `prop_fts_gin` stays one release for old pods, and is dropped in T‑1070g. Changing the config or fields now means replacing the trigger function and re‑running the backfill.

### ADR‑021 addendum — T‑1032c invitations (2026-11-23)
- **Owner decision (KI‑041):** an invite grants nothing until the invitee accepts.
- **Model:** `OrganizationInvite` (pending/accepted/declined/revoked/expired). It is separate from `OrganizationMembership`, so access checks, which all key on an active membership, never see a pending invite. A partial unique constraint allows one pending invite per org + user. It expires after `ORG_INVITE_TTL_DAYS` (default 7): expiry is evaluated on read and on respond, with no beat job.
- **Policy:**
  - `check_invite` runs at invite time; owner is never grantable, and org_admin is owner‑only.
  - At acceptance, the membership is created through `invite_member` **with the inviter as actor**, so the grant is re‑checked against the inviter's current rights. An inviter who has left or been demoted voids the invite (status `revoked`, 409).
  - Only the invitee can respond; another user's id → 404, which reveals nothing.
  - Org managers list and revoke invites.
  - Each step writes an `AuditLog` row (`membership.invite_created/accepted/declined/voided/revoked`). The invite itself creates no membership row.
- **Notice:** a WhatsApp text through the outbox, in the same transaction, keyed `org_invite:<id>`, sent with the org's credentials.
- **API change:** `POST /organizations/me/members/` returns the invite (`status: pending`) instead of a member. New endpoints: `GET/DELETE /organizations/me/invites/[{id}/]`, `GET /organizations/invites/`, `POST /organizations/invites/{id}/accept|decline/`.
- **Transactions:** a refused response that changes state (expired, voided) commits that change and raises the 409 *after* the transaction, so the state change is not rolled back.

## ADR‑024 — Location hierarchy: own tree, text matching first, geo and geocoding later
- **Date:** 2026-11-23 (session 9) · Task T‑1060 · FR‑LST‑003, GAP‑013/041, TR‑05
- **Context:** listings carry free‑text `city`/`location` plus optional lat/lng. The owner chose Google geocoding and PostGIS, but neither PostGIS nor a key exists in any environment yet.
- **Decision:** split T‑1060 so that the first slice needs no new infrastructure.
  - **T‑1060a, this ADR:** a `locations.Location` tree per market (country → region → city → locality → sub‑locality).
    - A materialised slug `path` (unique) makes "this place and everything below it" one indexed prefix query. Renames rewrite descendant paths.
    - Listings get a nullable `location_ref` FK (`SET_NULL`, index built concurrently) and `location_precision`. `city`/`location` stay as the listed text.
    - **Matching** is deterministic text matching on a normalised key: case, punctuation, `ph`→`phase`, leading zeros, and joined single letters, so `D.H.A.` = `DHA`. Aliases count as names.
      - A city is matched by name or alias.
      - Under it: an exact locality or sub‑locality, else the longest locality that prefixes the text, whose remainder becomes a sub‑locality.
      - An unknown place becomes an **unverified** node (`source=listing`). This is the review queue. Admin can verify it, or merge it: the merged name becomes an alias of the target, and listings and children move.
    - **Where matching runs:** API create/update (only when the client sent no `location_id`, and on update only if the text or market changed), `match_listing_locations` (batched, with a coverage report for the M1 exit criterion), and an hourly beat job for writes from elsewhere (AI tool, scrapers, admin).
    - **Not in `Property.save()`:** matching can create rows, and doing it in `save()` would add queries and side effects to every save, including test factories.
  - **T‑1060b:** a PostGIS `geography(Point)` with a GiST index and radius/bbox search, plus privacy masking for private sellers. This is the step that requires PostGIS everywhere.
  - **T‑1060c:** a Google geocoder behind an interface and a flag (default off) that geocodes **location centroids**, not each listing, to bound cost.
- **Seed:** 5 countries; PK provinces/territories, the 24 cities of the ref‑number map and well‑known Lahore/Karachi/Islamabad localities; AE emirates; GB nations and major cities. The normaliser in the seed is a frozen copy, and a test keeps it equal to the live one.
- **API:**
  - `location_id` (write) and `location_ref` (read, `{id, name, level, path}`) on listings; a location must be in the listing's market;
  - `?location_id=` matches the location and every descendant;
  - `GET /locations/?market&parent&level&q` for browse and autocomplete. Unverified nodes are staff‑only.
- **Rejected:**
  - geocoding every listing: cost, and the text already names the place;
  - `django-mptt` / `ltree`: a materialised path covers the queries without a new dependency or extension;
  - auto‑verifying matched text: free text is noisy, so a human reviews new places.

### ADR‑023 addendum — T‑1070g contract (2026‑11‑23, session 9)
- **Owner decisions:** the contract is approved, and money stays in major units (`BigInteger` + ISO currency; no minor‑unit conversion).
- **Two releases instead of one drop.** The model can only stop selecting a column once no running pod needs it, and the previous release still reads the v1 columns.
  - **g‑1, `properties 0040/0041`:** a state‑only removal of `area_marla`/`area_unit`/`area_sqm`/`property_type`, their index and `prop_fts_gin`, using `SeparateDatabaseAndState`.
    - A BEFORE INSERT/UPDATE trigger keeps the v1 columns correct for older pods. v2 is authoritative, except when a write changed only the v1 pair (an older pod), in which case v1 → v2.
    - 0041 repairs pre‑trigger drift in batches. v1 wins, as the old repair commands did.
    - 0041 also normalises categories that were never valid (`house`, `flat` …), which came from the unvalidated v1 `property_type`.
    - This replaces the Python dual‑write and the repair commands.
  - **g‑2:** drop the trigger, the columns and (concurrently) `prop_fts_gin`.
    - Staged outside `migrations/` so it cannot ship early. Moved in one release later.
    - Its first operation is the same gate as `check_listing_contract`: g‑1 applied, trigger present, zero drift, no unknown category. It refuses otherwise.
    - The reverse restores the columns from v2.
- **Kept:**
  - `Property.property_type` as a derived read‑only property, with no setter, so stray writers fail loudly;
  - the API names `area_marla`/`area_unit`/`area_sqm`/`property_type`, as deprecated aliases computed from and mapped onto v2 (removing them is an API‑version change);
  - the search/AI/scraper DTO and parameter names (`area_marla` = marla‑equivalent search size, `property_type` = coarse type), because they are not DB columns, and they are also Celery kwargs and LLM tool schemas that must stay stable across rollouts.
- **Ordering rule:** g‑1 migrations must be applied **before** any g‑1 pod serves traffic. g‑1 inserts rely on the trigger to fill the NOT NULL v1 columns. The previous release is unaffected by 0040/0041.

### ADR‑024 addendum — T‑1060b geo search without a hard PostGIS dependency (2026‑11‑23)
- **Context:** the owner chose PostGIS, but no environment has it yet, and deploys must not be blocked.
- **Decision:**
  - **No GeoDjango.** That means no GDAL in the images, no DB engine switch and no `geography` column, for now.
  - Listings get a **public point** (`geo_lat`/`geo_lng`, decimals), maintained by a trigger. It is exact only for non‑private listings marked exact; otherwise it is rounded to 2 dp (~1.1 km).
  - **All geo queries and every non‑privileged response use the public point.** Exact coordinates never answer a radius query, so they cannot be triangulated.
  - **Backends:**
    - default: bbox on the btree index `prop_geo_point_idx`, then haversine in SQL;
    - with PostGIS (`geo_setup` + `GEO_POSTGIS_ENABLED`): `ST_DWithin`/`ST_Distance` on a GiST index over `ST_MakePoint(geo_lng, geo_lat)::geography`, an expression on the same columns, so nothing needs backfilling.
- **Measured** (200k rows, local): both paths use their index; 5 km returns 18 rows in about 1 ms, 25 km returns 208 rows in 4–5 ms. Results are the same, and distances agree within 0.5 % (sphere vs spheroid).
- **When to adopt GeoDjango:** for polygon search or areas on `Location` (later). It would add a `geography` column from the same public point, under the same expand/contract rules.
- **Rejected:**
  - switching the engine to `postgis` now: every deploy would need PostGIS first;
  - Python‑side haversine, as the pin resolver did: a full scan per request;
  - masking only in the serializer: radius filters on exact coordinates leak location.

### ADR‑024 addendum — T‑1060c geocoding (2026‑11‑23)
- **Decision:** geocode only `Location` nodes (city/locality/sub‑locality), never individual listings.
  - A listing without its own coordinates takes its location's centroid through public‑point trigger v2 (`geo 0001`). It is always snapped, and `location_precision` stays approximate.
  - Cost is bounded by the number of places, not by write volume. There is a daily request budget and a result cache.
- **Provider:** Google Geocoding (owner's choice), behind a small interface (`geocode(query, country)`). Default off: `GEOCODING_ENABLED`, plus the key.
- **Trust:**
  - A result must be in the market's country and not coarser than the level asked for.
  - It must be within 60 km of the nearest geocoded ancestor; this catches "DHA" matched in the wrong city.
  - Partial matches go to review, and their centroid is not stored.
  - Admin‑entered coordinates are `manual` and never overwritten.
- **Google Maps Platform terms (owner/legal to confirm):**
  - Coordinates may be cached for at most 30 consecutive days. Centroids are re‑geocoded after `GEOCODING_REFRESH_DAYS` (29), and the place id is kept.
  - Geocoding results shown on a map must be shown on a Google map. If a frontend shows listing or location points on another map provider, that conflicts with the terms.
- **Security:** the key is a query parameter. `requests` exceptions include the URL, so errors are logged and stored by type only, with no cause chained.

### ADR‑025 — Listing lifecycle (T‑1080, 2026‑11‑23)
- **Decision:** `Property.status` is the source of truth, with 9 states (PRD FR‑LST‑005) and a DB CHECK constraint. `is_active` stays as a **derived** "publicly visible" flag (`status ∈ {published, under_offer}`).
  - About 30 readers (`visible_to`, search, AI tools, reports, escrow) keep working unchanged, as do the existing `is_active` indexes.
  - Trigger `properties_status_trg` keeps the pair consistent for every writer: `QuerySet.update`, raw SQL, and pods of the previous release that only write `is_active` (`false` → archived, `true` → published). `Property._sync_status` mirrors it in memory.
- **Transitions** go through `apps.properties.lifecycle.transition`: edge table, role checks, publish preconditions, row lock, timestamps. Every ORM status change writes a `ListingTransition` row (source api/admin/system/create/direct). `QuerySet.update(status=…)` is consistent but **not audited**; code must not use it.
- **Compatibility:**
  - A new listing still starts as `published` unless the request asks for `draft`/`pending_review`. A market with `listing_review_required` turns published into pending_review.
  - PATCH `is_active` still works, mapped to an audited transition.
  - Owners and listing agents now also see their own non‑public listings on detail and with `?status=`.
- **Expiry is per market** (`Market.settings.listing_ttl_days`) and **off by default**. Existing listings get no `expires_at`, so nothing vanishes on deploy. Reminders go out once, `LISTING_EXPIRY_REMINDER_DAYS` (7) before expiry, through `notify_user` (outbox).
- **Not done here:** an outbox domain event per transition (no consumer exists yet; add a kind when one does); the "at least one image" publish rule (FR‑LST business rule, market‑configurable); the moderation queue of FR‑LST‑009.

### ADR‑026 — MFA (T‑1011, 2026‑11‑23)
- **Decision:** TOTP (RFC 6238, SHA‑1/30 s/6 digits) with recovery codes, implemented in the standard library (`apps/users/mfa.py`); no new dependency. The QR code uses the existing `qrcode` package.
- **Scope:** required for platform staff (role admin, is_staff, superuser) and org owners and org admins; optional for everyone else. Enforcement is a flag (`MFA_ENFORCE`, default off) so these users can enrol before it bites. While it's off they log in as before, and the response says enrolment is required.
- **One login gate:** every view that issues tokens calls `mfa.login_response`. An enrolled user gets a signed, single‑use, 5‑minute `mfa_token` instead of tokens. When enforced, a required user who has not enrolled gets an enrolment‑scoped `mfa_token` that only allows setup and confirm.
- **Proof in the token:** tokens issued after the second factor carry `mfa: true`. The JWT authentication class and the refresh endpoint refuse a token without it for an enrolled user (and, when enforced, for a required user), so older sessions and any path that skipped the second step stop working. `MfaLenientAuthentication` (enrolment endpoints only) treats such a token as anonymous instead.
- **Abuse limits:** a code step is accepted once (`last_used_step`); 5 wrong codes lock the second step for 15 minutes; the `mfa_verify` throttle is 20/min per IP. Recovery codes are stored as keyed HMACs (`blind_index`), and the TOTP secret is encrypted at rest.
- **Django admin:** the login form asks for the authenticator code (or a recovery code). When enforced, staff without MFA must enrol through the app first.
- **Recovery:** an admin action resets MFA (audited, all sessions revoked). Users for whom MFA is required cannot disable it themselves.
- **Side effect:** the refresh endpoint now also refuses a refresh token for a deactivated user.

### ADR‑027 — Image upload sanitisation (T‑1110, 2026‑11‑23)
- **Decision:** every user‑uploaded image is **decoded and re‑encoded** by `apps.core.images.sanitize_image` before storage; the original bytes are never stored. This removes, in one mechanism:
  - EXIF/GPS and other metadata (privacy: home locations of private sellers);
  - polyglot or appended payloads;
  - mismatched content types.
- **Trust nothing from the client:** the format comes from the magic bytes, and only JPEG, PNG and WebP are accepted (no SVG, GIF or HEIC). The storage key is `<uuid>.<ext of the detected format>`; `upload_to` functions use `safe_upload_name`, which maps anything else to `.bin`. Error messages never echo client file names.
- **Resource limits:**
  - bounded read (`IMAGE_MAX_BYTES`);
  - pixel budget checked from the header before decoding (`IMAGE_MAX_PIXELS`), plus Pillow's bomb errors;
  - a full decode, so truncated files fail;
  - downscale to `IMAGE_MAX_SIDE`;
  - listing count limit enforced under a row lock.
- **Kept:** the ICC colour profile; the EXIF orientation is applied to the pixels; animated images keep their first frame.
- **Thumbnails** are generated at upload (`IMAGE_THUMB_SIDE`) so they work with plain file storage. With Cloudinary the same files are stored; delivery transformations remain possible.
- **Existing files:** `manage.py sanitize_images` re‑encodes pre‑T‑1110 listing images (`sha256 = ''`) and, with `--include-profiles`, agent photos and org logos.
- **Not in scope:** documents (PDF) — content sniffing and malware scanning for the verification uploads remain FR‑LST‑007 / a follow‑up; video, tours and floor plans (P2).

### ADR‑028 — LLM router and prompt registry (T‑1120, 2026‑11‑23)
- **Decision:** product code calls models only through `apps.ai.llm` (`complete`, `complete_json`, `vision`, `chat`). Prompts are named, versioned `Prompt` objects in `apps.ai.prompts`. Each `AIInteraction` row records `provider`, `prompt_name`, `prompt_version` and `prompt_digest` (the first 12 hex characters of the template's sha256).
- **Providers:** `gemini`, `local` (Ollama) and `openai` (any OpenAI‑compatible `/v1/chat/completions` server). They share the `AIBackend` interface plus a new `complete()` that honours max tokens, temperature, JSON mode and a timeout.
- **Failover:**
  - `AI_PROVIDER_ORDER` defaults to `[AI_BACKEND]`: a single provider, so behaviour and cost are unchanged.
  - Unconfigured providers are skipped.
  - A per‑provider circuit breaker in the cache (`AI_BREAKER_FAILURES` within `AI_BREAKER_SECONDS`) moves a failing provider to the end of the order instead of dropping it, so even a total outage gets one last try.
  - Failover happens only on exceptions, never on a bad answer.
  - SDK retries are off (`max_retries=0`); the router is the retry layer.
- **Side effects:** a chat turn never fails over once any tool has run (tools are wrapped to record execution). Re‑running the turn on another provider could create a second listing, lead or deal lock.
- **Versioning rule:** changing a template means bumping its version.
  - `tests/test_llm.py` pins the versions, and re‑registering the same version with different text raises.
  - Templates are market‑neutral: a test forbids Pakistan/PKR/marla/CNIC/Urdu outside the `market.*` fragments.
  - Market facts come from `market_vars()` (name, currency, area unit, price format, tax system) and from the market packs' `MARKET_KNOWLEDGE`, registered as `market.<CC>.knowledge` (`KNOWLEDGE_VERSION`, default 1).
- **Token accounting:** unchanged. Backends still report usage to `apps.ai.usage`; the router only adds provenance.
- **Not in scope (KI‑059):** these are not yet routed or registered. The explicit Gemini tool loop is T‑2010.
  - document OCR in `RealTronAgent.verify_document_image` (market‑pack `ocr_prompt`);
  - audio transcription (the `STTService` cascade);
  - the intent classifier's prompts.
