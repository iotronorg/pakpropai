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

### ADR‑013 addendum — Phase C, staged (T‑1032b, 2026‑11‑23)
- **Owner decision:** the contraction is staged like T‑1070g, and `Agent.organization` stays as a derived primary‑org pointer.
- **b‑1 (this release):**
  - **The owner membership is the source of truth.** Readers use `membership.owner_of` (`Organization.owner`) and `owned_organization_ids`. Org registration and platform‑admin create/PATCH write through `set_owner`.
  - **`admin_user`** remains a projection for older builds, written by `sync_admin_projection`. Because the column is one‑to‑one, an owner who already projects into another org leaves it NULL. The legacy post_save hook still mirrors direct `admin_user` writes (Django admin, factories).
  - **Multi‑org owners:** a user may own several orgs. `transfer_ownership` no longer refuses a target who owns another org, and a freed projection moves to the previous owner's next org.
  - **Collaboration terms:** `AgentOrgMembership.membership` links the terms to the canonical membership (derived on save, backfilled in `agents 0015`). Lead routing ignores terms whose membership was deactivated.
  - **Legacy table:** `AgentOrganizationMembership` (no readers) left the model state; its table and rows stay until b‑2. Its two FK constraints are dropped in `agents 0015`, because otherwise a TRUNCATE of `organizations` (Django test flushes) fails on a table Django no longer knows about.
- **Drift semantics:**
  - `missing_owner` now fires only for an org with no owner membership. It previously also promoted a stale `admin_user` to a second owner.
  - A mismatched projection is `stale_admin_projection` (repairable).
  - `extra_owner` means more than one active owner.
  - `unlinked_collaboration` is repairable.
  - `CONTRACT_BLOCKERS` gate b‑2.
- **b‑2 (staged):** `organizations/contract_migrations/0029` drops `admin_user_id` (and removes it from the state); `agents/contract_migrations/0016` drops `agent_organization_memberships`. Each refuses while any blocker is reported, and each reverse restores (the projection is refilled from owner memberships). The README lists the code removals that must ship in the same release. A guard test keeps `admin_user` out of every module except the projection's own.
- **Rejected:** a DB trigger mirroring owner memberships into `admin_user` (as T‑1070g did). There is a single application write path, and the one‑to‑one constraint means the projection cannot be total anyway.

### ADR‑002 addendum — Leads and WhatsApp sessions follow their organization (T‑1040, 2026‑11‑23)
- **Context (KI‑017):**
  - Leads and sessions derived the flag only on insert, and only when an org was set. Unscoped (AI‑queue) leads kept the default `True`.
  - A lead that got its org later kept whatever it had (upsert with an org, agent assignment, the WhatsApp router moving a session to an org's number). A production org could therefore own "sandbox" leads that the go‑live provisioner had already passed over.
- **Decision:**
  - The `SandboxFollowsOrg` mixin (in `apps/organizations/sandbox.py`) derives `is_sandbox` on insert (any caller value is ignored) and on every save that changes `organization`.
    - It is `update_fields`‑aware: it adds `is_sandbox` when `organization` is in the list, and does nothing when the org field was not loaded.
  - `contact_is_sandbox(org_id)`: no organization means **live**. There is no platform‑level sandbox, and an unscoped lead is a real person on the shared number (as for private‑client listings).
  - A change of the org's own `operational_mode` still does not re‑derive: going live is the provisioner's job.
- **Data:** `leads 0021` and `whatsapp 0013` are batched, non‑atomic and idempotent. They set `False` for rows with no org or with a production org. Rows of non‑production orgs are untouched.
- **Not covered:** `QuerySet.update(organization=…)` and `bulk_create` bypass `save()`. No such code exists today.

### ADR‑029 — Explicit tool‑loop runtime and tool registry (T‑2010, 2026‑11‑23)
- **Context:**
  - Gemini ran tools inside the SDK (Automatic Function Calling). Usage covered only the last round (the KI‑006 remainder), and tool calls were invisible to the application.
  - Ollama had its own hand‑written loop. Rules such as "return the agent contact verbatim" therefore applied to one provider only.
- **Decision:**
  - Providers implement only `AIBackend.step(system, messages, tools)`, which returns text and/or `ToolCall`s. Gemini sets `AutomaticFunctionCallingConfig(disable=True)` and declares tools with `parameters_json_schema`; OpenAI‑compatible servers use `tools`.
  - `apps/ai/runtime.run_turn` owns the loop:
    - at most `AI_MAX_TOOL_STEPS` (5) model calls, per the AI spec;
    - argument validation through the registry;
    - execution with the existing request context;
    - error results instead of exceptions;
    - terminal tools end the turn with the tool's own summary;
    - per‑step usage;
    - an audit record per call.
  - Messages are provider‑neutral. A provider's raw assistant content is replayed only to that same provider (Gemini thought signatures).
- **Tool registry (`apps/ai/tool_registry.py`):**
  - A `ToolSpec` carries the policy fields from AI spec §5: JSON schema (from signature and docstring), side‑effect class, `requires_confirmation`, feature flag with its default (deal lock is off by default), terminal, timeout, markets.
  - Functions are resolved on `apps.ai.tools` at call time.
  - Plain callables are wrapped as conservative ad‑hoc specs that count as committing.
- **Failover rule refined:** a turn may be re‑run on the next provider after read‑only tools. Only a *successful* tool with side‑effect class `low`, `contact_share`, `booking` or `financial` commits the turn; a later provider failure then raises `TurnCommitted`. This replaces T‑1120's "any tool ran".
- **Unchanged:**
  - Confirmations are still enforced inside the tools (cache‑based, T‑0007); T‑2012 makes them durable and runtime‑enforced.
  - The `_is_agent_request` keyword bypass stays until then.
  - Per‑tool `timeout_ms` is declared but not enforced; tools run synchronously in the request (T‑2011/T‑2020).

### ADR‑030 — Durable confirmations (T‑2012, 2026‑11‑24)
- **Context:** proposals lived in the cache, keyed by phone and tool (not org). Execution needed the model to call the tool again with the same arguments. Two keyword routes skipped confirmation altogether. Supersedes ADR‑005's mechanism.
- **Decision:**
  - The runtime turns a `requires_confirmation` call into a `PendingAction` and ends the turn with its summary verbatim. Later calls in the same step are skipped.
  - The **stored** arguments run, never ones the model sends later. Args and prompt are encrypted; an args hash is verified at execution, and the feature flag and market are re‑checked.
  - Only the very next message can confirm by text, and only if the whole message is a yes. Anything else, including an image, agent takeover or reset, closes that window. The API can still confirm by id until expiry (30 min).
  - One open action per conversation (partial unique index). Execution is at most once (conditional pending → executing update); a crash mid‑run is marked failed, not retried.
  - If the reply sent differs from the stored prompt, the action is withdrawn. Proposals skip the grounding check (they restate the user's own details) but not the output guard.
  - A tool without a proposal builder (`apps/ai/proposals.py`) cannot be proposed: it fails closed.
- **Consequences:** asking for an agent or an audit now takes an extra YES. The agent route needs a working model. Prompts are English only until the message catalogue exists. If WhatsApp delivery of the summary fails, the action stays open until expiry.
- **Later:** `conversation_key` becomes a `Conversation` FK in T‑2020; new committing tools must add a builder.

### ADR‑031 — AI traces and cost (T‑2011, 2026‑11‑24)
- **Decision:**
  - One `AITrace` per assistant turn, written by `AIServiceManager.process`. It is a separate table from `AIInteraction` (per call, with text) and holds no message text, tool arguments or phone.
  - `ModelPrice` rows are entered by operators with an effective date; none are seeded. A turn with any unreported or unpriced call, or with mixed currencies, has no cost, and the report shows how many such turns there are.
  - Cost is stored at the time of the turn; later price rows do not rewrite history.
  - A "conversation" in the report is one contact in one org within the period, until T‑2020. Qualified leads are non‑sandbox leads created in the period whose status is `qualified` now.
- **Not done:** cost‑spike alerts, dashboards, and cost of one‑shot calls outside a turn. Per‑tool `timeout_ms` is still not enforced.

### ADR‑032 — Grounding checker v2 (T‑2013, 2026‑11‑24)
- **Context:** the T‑0009 check covered one listing in focus for 60 s, only the largest price, and city/instalments. Multi‑result replies, sizes, rooms and amenities were unchecked.
- **Decision:**
  - The runtime checks each model‑written reply against this turn's listing results, or the conversation's last results (30 min) when the turn has none. Own listings are re‑read from the database.
  - Claims are checked per listing: a block naming a listing (title, else location) is compared with that listing; other blocks with all of them. Price tolerance 1 %, size 2 %.
  - Numbers from non‑listing tools, the system prompt and the user's budget are not listing claims. A user's number cannot be confirmed as a listing's price. Tool arguments are never evidence.
  - One regeneration without tools, then a facts‑only reply plus an offer of an agent. If the checker itself fails, the reply is not sent.
  - The prompt's inventory sample carries names only.
- **Supersedes** T‑0009 for runtime replies; the old check still runs when the runtime did not.
- **Limits:** regex extraction. Negated or question lines skip the amenity, room and verification checks; words with no digits ("three bedrooms") and non‑English amenity names are not extracted; image replies and copilot drafts follow their own paths (drafts use the same runtime; images do not). Eval coverage is T‑2070.

### ADR‑033 — Durable conversations and conversation API (T‑2020, 2026‑11‑24)
- **Context:** chat history lived only in Redis (24 h, per org + phone), and there was no web chat API.
- **Decision:**
  - `apps.conversations`: `Conversation`, `Message` (content encrypted, ordered by `seq`), `ConversationState`. Written only through `store`. Postgres is the source; Redis holds the 40‑message window and is rebuilt on loss.
  - `key` is the scope used by history, confirmations, grounding and traces: `<org|global>:<phone>` on WhatsApp, `global:web-<id>` on web. A context variable carries the current conversation, so web and WhatsApp never share a history or a pending "yes". One open conversation per key; a reset, or a different user on the number, opens a new one.
  - `AIServiceManager.process` stores the user message first, then the reply with its trace, citations and pending action. Storage failures are logged and never cost a WhatsApp reply; the web API returns 503. Non‑interactive calls only read. Guard‑blocked turns are kept but not shown to the model again.
  - SSE sends `accepted`, `pending_action`, `card`, `final` or `error`. No token streaming: the grounding check (ADR‑032) needs the full reply.
  - Web: own conversations only (404 otherwise), one turn at a time, `client_message_id` for retries, 10/min and 200/day per user, blocked users refused. Web tools never send WhatsApp follow‑ups or change the WhatsApp session.
  - Retention: `Market.settings["conversation_retention_days"]`, else `CONVERSATION_RETENTION_DAYS` (365). Deleting cascades to messages, state and pending actions.
- **Not included:** org‑side access to conversations, attachments, org‑hosted web chat, merging `WhatsAppMessage`/`ConversationMessage` (FR‑COM‑009), rolling summaries. PII masking still applies to message text.

### ADR‑034 — SearchCriteria and search v2 (T‑2030, 2026‑11‑24)
- **Context:** search took PK‑shaped arguments (marla, city text), ranked by `ai_score`, and scraped portals when enabled.
- **Decision:**
  - One `SearchCriteria` (AI spec §4.1, strict pydantic) validated by `criteria.prepare`; one `listing_search.execute` for REST, AI tools and later alerts.
  - Hard filters first. The score is computed in SQL from weighted factors; only factors the criteria make meaningful count. Weights: `Market.settings["ranking_weights"]`; `ranking_version` names them. No paid boost; `sponsored` is always false for now.
  - Reasons (top 3) and one trade‑off are templates over stored fields.
  - Price filters compare one currency and one period (per year ↔ per month × 12). Price‑on‑request listings never match a price filter or a price order, so a hidden price cannot be probed.
  - Place names resolve on the location tree; a shared name is ambiguous (REST 422, the assistant asks). Unmatched listings fall back to their own text.
  - Markets that are `inactive` are refused; `beta` markets are searchable.
  - Clarification is decided by the server: location is required, purpose is asked once, at most two questions. Defaults are returned as `assumed` and stated to the user.
  - Subjective or protected wording and "near a metro" are removed from filters and ranking and reported as unsupported (heuristic, English plus a few Urdu words).
  - Cursors are signed, expire after a day, and are bound to the criteria, sort, ranking version and viewer.
- **Assistant:** `search_listings`, `update_session_criteria` and `get_listing` replace `search_properties`, so **chat no longer scrapes portals** (FR‑SRCH‑011) and no longer asks the model for verdicts. The old service code stays.
- **Limits:** no semantic search, POI distance, polygon or FX; alerts are T‑2040; extraction accuracy is measured in T‑2070.

### ADR‑035 — Public listing read API (T‑2031, 2026‑11‑24)
- **Decision:**
  - `GET /api/v1/public/listings/search/` maps query parameters to `SearchCriteria` and uses the same engine as `POST /search/`; `GET …/{slug_or_id}/` accepts an id, a reference number or a slug.
  - The slug is derived (title + reference number). The reference number identifies the listing, so old slugs keep working after a title change; the answer carries the canonical slug. No column, no migration.
  - Fields are listed one by one and an exact‑set test guards them. Never shown: owner, staff, contacts, exact coordinates, scores, raw documents, hidden prices and their deposit or service charge, attributes outside the market schema, unsanitised images.
  - Phone numbers and emails are removed from the title, description and alt text of listings that do not belong to an organization (also in search results and the assistant's tools).
  - Credentials are ignored, so every caller gets the same answer. `Cache-Control: public, max-age=60`, weak ETag, 304. A withdrawn listing can stay in a cache for up to 60 s. Not‑public is always 404.
- **Limits:** contact redaction is a pattern (10–15 digits, emails); numbers written in words pass. No sitemap or 410 for past listings (SEO pages are P2).

### ADR‑036 — Enquiries, threads and email (T‑2050, 2026‑11‑24)
- **Decision:**
  - One `Enquiry` row is both the consented enquiry and its thread (the spec's `Thread` and `ThreadParticipant` are not separate tables). Messages are `ConversationMessage` rows with `enquiry` set, so the CRM and the thread share one immutable log (towards FR‑COM‑009).
  - `contact_consent: true` is required. The consent version, time and channel are stored; consent can be withdrawn. Contacts are shown, and contact details in messages unmasked, only while **both** sides share.
  - Who answers: organization managers or the assigned agent (active membership), the freelance agent, the private owner, or platform admins for requests without a listing. Everyone else gets 404, including platform admins for organization threads.
  - One lead per person and organization. Enquiries to private owners, freelancers and the platform use leads of their own; WhatsApp chats, `upsert_lead` and `connect_to_agent` never use those or another organization's lead.
  - One open enquiry per person and listing (database constraint); a second one continues the thread. At most 20 new enquiries per day.
  - The assistant's `create_enquiry` runs only after the user's confirmation; the listing is stored by id at proposal time. `connect_to_agent` stays for requests without a listing.
  - Notifications contain no message text or contact details, and are sent once per unread burst (again after 1 h). Email goes through the outbox to any SMTP provider; address and preferences are checked at delivery.
- **Kept:** the owner's rule that free‑text messages are PII‑masked on input (T‑1033): emails and `+` phone numbers typed in a message are never stored.
- **Limits:** KI‑072…KI‑075. Email addresses are not verified, which is why emails carry no content.

### ADR‑037 — Availability and viewings (T‑2060, 2026‑11‑24)
- **Decision:**
  - Agents keep weekly rules and dated exceptions in wall‑clock time with an IANA zone; slots are computed in UTC, not stored (no `ViewingSlot` table). A slot id is `<listing>@<UTC start>` and is checked again when booked. Times skipped by a clock change give no slot.
  - Who shows a listing: the visitor's assigned agent at that organization, else the listing's agent, else any active member agent with hours. Private owners have no slots; the thread is used.
  - Every change of an agent's calendar (visitor booking, reschedule, dashboard create, edit, reschedule) takes a row lock on the agent and checks for overlap. Dashboard double bookings now get 409.
  - A booking creates or continues a consented enquiry, so consent, blocking and limits are those of ADR‑036. Limits: 2 h notice, 60 days ahead, 5 upcoming per person, one per listing, no overlap.
  - The assistant books only after the user's confirmation and only a time that is really free; the listing and UTC start are stored at proposal time.
  - Reminders a day and an hour before, to visitor and agent, by their preferences, claimed once per time; moving a visit resets them. Feedback (1–5) from each side after the visit; the visitor is asked once.
- **Limits:** KI‑076, KI‑077; calendar sync and ICS are P2 (FR‑APT‑007). The lock protects all API paths; direct database or admin edits are not checked.

### ADR‑038 — Favourites, saved searches and preference memory (T‑2040, 2026‑11‑24)
- **Decision:**
  - New app `apps/preferences`; everything is private to the person, under `/api/v1/me/`. Favourites keep only a link: a listing that is no longer public shows no data.
  - A saved search stores prepared criteria and the consent (time, source). Unknown required amenities are refused instead of dropped, which would widen the alert.
  - Alerts run every 15 minutes: one locked transaction per search marks listings (`AlertDelivery`, unique) and queues the notification; if that fails the marks roll back and it retries after an hour. Instant means at most hourly. Not announced: own, saved, dismissed, already announced, or anything from a pause. A withdrawn place or market pauses the search.
  - `notify_user` now returns whether it worked. Alerts have their own preference (`saved_searches`) and a signed stop link that can only pause that search.
  - Preferences need the `ai_memory` consent, which has its own endpoint because withdrawing deletes the profile. Only structured criteria fields; no free text, no accessibility needs. Items are stated or inferred (3+ saved listings, 60 % agreement), and deleted after `PREFERENCE_RETENTION_DAYS` (180).
  - The assistant uses only stated items, only to fill a missing place or purpose or "my budget", and must say so. `create_saved_search` and `remember_preferences` run after YES on criteria frozen at proposal time; `save_listing` is reversible and needs no confirmation.
- **Limits:** KI‑078…KI‑080.

### ADR‑039 — Comparison and approximate FX (T‑2041, 2026‑11‑24)
- **Decision:**
  - `GET /api/v1/public/listings/compare/` (anonymous, cacheable, same answer for everyone) and `GET /api/v1/compare/` (signed in; organization sandbox listings; own stated preferences only with `use_preferences=true` and consent). 2–4 listings; unavailable ones are listed. The agent‑only `properties/compare/` is unchanged.
  - Only stored facts and arithmetic: rents per month, price per m², amenities diff, age, verification. Null means not specified. A hidden price is hidden in every derived value and gives "unknown" in the match.
  - The match uses the search engine's own filter one condition at a time, so it agrees with search; answers are yes, no or unknown.
  - "Best" values exist only when every listing has a comparable value and one clearly wins. There is no overall winner.
  - `FxRate` holds daily units per USD with source; conversion is for display, labelled approximate with the oldest date used, and not done with rates older than 7 days. Stored prices are never changed.
  - `compare_listings` is read‑only. Derived amounts are grounded per listing and currency, not in the turn‑wide list.
- **Limits:** KI‑081, KI‑082. No `fx/rates` endpoint and no cross‑currency filters (FR‑SRCH‑009); narrative and running costs are P2 (FR‑CMP‑003/004).

### ADR‑040 — Listing moderation (T‑2014, 2026‑11‑24)
- **Decision (owner):** private people's listings wait for review; organization listings publish directly unless an automated check flags them. `listing_review_required` still reviews everyone in a market.
- **How:**
  - One rule, `lifecycle.review_required`, for every path. An approval is bound to a digest of the text, so an unchanged listing can be renewed without a second review. A suspended listing cannot return through the archive.
  - Checks give codes only (prohibited content, external link, price outlier against 10+ comparables, possible duplicate); a person decides. A flagged edit hides a public listing until reviewed; another text edit of a private listing is queued without hiding it.
  - One open item per listing, opened and closed by a signal on `ListingTransition`, so Django admin decisions count too. Priority, 24 h deadline, one overdue notice.
  - A decision needs the digest the moderator saw (409 if changed), a reason for reject/suspend, and never concerns the moderator's own listing. Reporters are not named. Reports never hide a listing; one appeal per suspension.
  - `list_property` saves a draft; `submit_listing` sends it to review; both run only after confirmation.
- **Limits:** KI‑086.

### ADR‑041 — Owner decisions of 2026‑11‑24
- **KI‑066:** data exports include assistant chats and thread messages, stored encrypted (`DataExportFile`), downloadable by the person for `DATA_EXPORT_DAYS` (7), then deleted. Cloudinary is no longer used.
- **KI‑074:** a lead with withdrawn consent and no other consented enquiry shows no phone in CRM lists, messages, appointments, activities, agent notifications and voice history; staff WhatsApp/voice and campaigns refuse or skip it. The thread still works.
- **KI‑065:** gunicorn gthread, 2 workers × 8 threads, 120 s.
- **KI‑081:** FX rates stay a manual import. Email provider and WhatsApp template stay go‑live items.

### ADR‑042 — Context‑free FAQ answers (T‑2016, 2026‑11‑24; replaces the policy of ADR‑003)
- **Decision:** a self‑contained general question of an organization's interactive chat is answered by its own prompt (`faq.answer` 1) from the question and the market knowledge only: no history, lead, user, organization settings or tools, and no user on the log row.
- **Who goes there:** intent `general_query`, starts as a question, names a real estate topic; no digits, links, personal words, references to the conversation or inventory words. Everything else, and `NOT_FAQ`, unsafe or failed answers, goes to the assistant.
- **Answer checks:** output guard, no links or addresses, every figure must be in the market facts.
- **Reuse:** 24 h, same organization, market, prompt digest, knowledge text, and **the same words in the same order** (case, punctuation, articles and "please" aside). Embedding similarity is not used: it cannot tell filer from non‑filer or buyer from seller, and exact words also stop one user's wording from changing another's answer. The cost is a lower hit rate.
- The switch stays `semantic_cache_enabled`, off by default.

### ADR‑043 — AI kill switch and eval gate (T‑2070, 2026-11-25)
- **Kill switch:** a row per stop of the platform, a market or an organization, in force until released, at most one open per target, kept as history and audited. Stopped and restarted by platform super, AI and operations admins (API) or `manage.py ai_kill_switch` (shell). Not by organizations.
- **What stops:** the whole turn (model, tools, deterministic routes, confirmations), every `apps.ai.llm` call including stored answers, document OCR and speech to text. Users get a fixed notice in their language; drafts are empty. A "yes" sent during a stop does nothing and cannot fire later; the open action can still be declined. A turn already running finishes.
- **State** is read from the database on each call (one query): no cache to lag, and an unreadable state fails the call.
- **Evals, two kinds.** *Offline* (safety, grounding, fairness, dataset): deterministic, no model, 100 % required, run in CI on every push. They test the layers that must hold whatever the model says. *Live*: production prompt and tool schemas against a real model, calls scored and never executed; field F1, money accuracy, tool accuracy, valid arguments ≥95 %, and at most 2 points below the recorded baseline. No baseline means no pass. Started by hand only, because it costs money.
- **Baseline** carries a fingerprint (prompt, knowledge, tools, cases); CI warns when it no longer matches. A run with provider errors is never accepted.
- **Why not `apps/ml/eval_harness.py`:** it grades with the assistant as its own judge, and falls back to 5.0 when the judge fails, so it cannot be a gate. Left unchanged.
- **Data:** the sets are seeds written by the developer, not native reviewers (T‑2072).

### ADR‑044 — Third-party text is data (T‑2071, 2026-11-25)
- **Where:** the runtime gives the model a cleaned copy of every tool result; `llm.render` cleans the prompt variables that carry people's text; names placed in the system prompt become one cleaned line. Evidence, cards, storage and audit keep the original. Only top-level `instruction`, `message` and `error` of a tool are ours and left alone.
- **How:** sentences with known attack wording, wording addressed to an assistant, or our tool names become `[removed]`; if an order is spread over sentences the whole text goes. `<` and `>` are replaced; descriptions are wrapped in `<lister_text>`; transcripts, inputs and listings sit between tags in their prompts. The trace records `indirect_injection`.
- **Output rule:** the assistant repeats a link, email, phone or account number only if the user, the conversation, the system prompt, a non-listing tool or a result's own `url` gave it. Otherwise the reply is regenerated or replaced (grounding code `contact`). An organization's phone inside its listing text is therefore not read out by the assistant; enquiries and agent hand-off are the way.
- **Moderation:** such a listing gets the flag `assistant_instructions` and waits for a person.
- **Limit:** patterns are not a boundary. Confirmation records, tool policy and grounding are (KI‑093).

### ADR‑045 — Photos, blocking and reports in threads (T‑2051, 2026-11-25)
- **Photos:** through the T‑1110 sanitiser, at most 3 per message, stored encrypted in the database (as data exports are, KI‑066) and served only through an authenticated endpoint; media storage would give public URLs. A photo can show a phone number, so photos are sent and seen only while both sides share contacts.
- **Block:** between a consumer and a lister (organization or person), by either side. Closes all their threads, ends the blocker's own sharing, refuses new enquiries, bookings and rescheduling. Only the blocking side can lift it. The blocked consumer sees "not available". The platform team cannot be blocked.
- **Report:** reporting lets a moderator read that thread unmasked, only while the report is open, never if they are part of it or work for that organization; every read is in the audit log. Reports never close a thread by themselves; a moderator dismisses or closes. The reporter is not named and the other side is not told.
- **Warnings:** codes for links, payment requests and one-time codes, computed on read and shown to the recipient only. They never hide or stop a message.

### ADR‑046 — End-to-end journey over HTTP (session 23, 2026-11-25)
- The journey talks to a running server only through its public API and signed webhook, and checks outcomes (threads, viewings, pending actions), never wording, so the same script runs locally with a stand-in model and on staging with the release model.
- Locally everything else is real: daphne, Celery workers started from the production entrypoint, Postgres, Redis. Stand-ins: the model and WhatsApp's API (`WA_API_URL`).
- `seed.py` runs only when `E2E_SEED_DATABASE` names the connected database, because it creates public listings.

### ADR‑047 — Masking covers forms, not provider callbacks (session 24, 2026-11-25)
- **Forms** are masked by the DRF parsers, with the same exempt paths and field allow-list as JSON. A test walks every URL and refuses an unmasked form parser.
- **Callbacks** (`payments|billing|verification|voice/webhook/`) reach the view unchanged, because a signature covers the exact body. Each must prove itself (signature, or reading the result from the provider), and payment callbacks are stored masked.
- **Jumio:** the callback is only a signal; the result comes from Jumio's API. Until it is readable the answer is 503, so Jumio repeats it.

### ADR‑048 — One rule for what consumers see (session 25, 2026-11-25)
- `visible_to()` is the whole rule: published or under offer, not expired, market not inactive, not sandbox (an organization still sees its own sandbox in its own context). Expiry is checked at read time, not by the daily job.
- "Live market" means not `inactive`: `beta` markets are shown, as before. `under_offer` listings are shown, as before.
- Owner phone, raw documents and AI analysis are for those who manage the listing or belong to its organization.
- When an organization goes live, its listings' `published_at` becomes that moment.

### ADR‑049 — Data at rest and data requests (session 26, 2026-11-25)
- **At rest:** secrets and national or tax IDs use encrypted fields; lookups use keyed hashes. A schema test refuses a new sensitive-looking plain column; `scan_plaintext` checks stored values.
- **Erasure** has one implementation. Each step is its own transaction; the account is blocked first and loses its phone number last, and only then is the request complete. Failures are repeated hourly and reported at 80% of the deadline. A test lists every relation to a person and what happens to it.
- **The phone number is removed**, so the person can register again. Payments and reservations stay, attached to an anonymous account.
- **Refused:** staff, organization owners (transfer first), people in an active reservation.
- **An organization** may erase only a person known to it alone; otherwise an administrator does it. The answer does not reveal whether a number is registered.

### ADR‑050 — Service-level objectives and how they are measured (session 27, 2026-11-25)
- **Objectives** live in `apps/core/slo.py` (`OBJECTIVES`), taken from PRD NFR‑PERF and MVP criteria 4 and 14, over 30 days: API availability 99.5% (non‑5xx); REST latency 95% ≤ 300 ms (search and AI turns excluded); search 95% ≤ 400 ms (`listing-search`, `public-listing-search`, `property-list`); model-written assistant replies 95% ≤ 8 s; WhatsApp webhook processing 99.5% without error and 95% picked up ≤ 5 s after receipt; enquiry notifications 99% delivered ≤ 60 s **per channel** (in‑app, WhatsApp, email; a failed delivery counts as late). Search and AI targets are PRD assumptions until the beta measures them.
- **Prometheus stays the measuring tool.** A metric lives in the process that observed it, so: `PROMETHEUS_MULTIPROC_DIR` for every multi-process server (gunicorn, Celery prefork), emptied at container start; Celery workers serve their own registry on `WORKER_METRICS_PORT` (`config/celery.py`), because WhatsApp AI replies, webhook processing and outbox deliveries run there. Before this, those metrics were never scraped (KI‑106).
- **Database readings where counters cannot be trusted:** the outbox backlog (`realtron_outbox_undelivered`, `…_oldest_undelivered_seconds`) is read at scrape time (one grouped query, cached 15 s per process); `manage.py notification_delivery_report` measures criterion 4 from `Notification.event` + outbox `sent_at`, independent of Prometheus restarts.
- **Alerts:** recorded error ratios per objective over 5 m, 30 m, 1 h and 6 h; multi-window burn-rate alerts (fast 14.4×, page; slow 6×, ticket); plus the enquiry backlog (> 60 s), stale outbox, target-down and "no worker scraped". Without traffic a ratio is empty, so idle hours raise nothing. `ops/tests/slo-rules.test.yaml` (promtool, in CI) proves they fire; `tests/test_slo.py` ties the rules, buckets, view names and dashboards to the code.
- **Bounded labels:** notification events are `enquiry.new|enquiry.message|enquiry.reply|other`.
- **Not decided here:** where alerts are sent (KI‑108) and how production runs Prometheus (KI‑109).

### ADR‑051 — Self-hosted monitoring stack, email alerts, open export, external files in erasure (session 28, 2026-11-25)
- **Monitoring** (owner: self-hosted Docker, deployable anywhere): `docker/docker-compose.monitoring.yml` runs pinned Prometheus, Alertmanager and Grafana. Combined with the prod compose file it shares the app network; alone, targets are private addresses in `ops/targets.prod/*.yml` (file discovery, so moving the app edits a file). Prometheus and Alertmanager have no login and bind to `MONITORING_BIND` (127.0.0.1); Grafana is the one page to publish.
- **Scrape authentication:** `/metrics/` accepts `Authorization: Bearer <PROMETHEUS_METRICS_TOKEN>` (≥ 16 characters, constant-time) besides the IP/CIDR list. Worker ports have no authentication and stay on a private network (`WORKER_METRICS_ADDR`). `/metrics/` and `/health/` skip tenant resolution (KI‑111).
- **Alerts by email** (owner): Alertmanager does not read environment variables, so `ops/alertmanager/start.sh` renders `alertmanager.email.yml.tmpl` from `ALERT_*`, rejects quotes, `|`, `&`, `\` and newlines, reads the SMTP password from a file, checks the result with `amtool` and starts. A critical alert silences the warning for the same objective.
- **Export for everyone** (owner): `POST /compliance/export/` (GET kept, deprecated) for any signed-in person, one open request per hour, queued after commit. Self-service deletion is unchanged (KI‑112).
- **External files in erasure** (owner): a first step deletes report and audit PDFs and WhatsApp media at Cloudinary by the public id read from the stored URL ("not found" = done). If it fails, the run stops before any later step removes the rows that hold the URLs, and the hourly job repeats it.

### ADR‑052 — Runbooks and the signals behind them (session 29, 2026-11-25)
- **Where:** `docs/runbooks/`: one file each for LLM outage, WhatsApp token expiry, data incident and harmful AI output, and `README.md` with short sections for the remaining alerts. Every alert carries a `runbook` annotation (a repository path, so it works wherever the stack runs); the alert email shows it. `tests/test_runbooks.py` fails if an alert has no runbook, a link or anchor is missing, or a runbook names a command or alert that does not exist.
- **New signals**, so each runbook starts from an alert rather than a report: `realtron_llm_provider_failures_total{provider,error}`, `realtron_llm_unavailable_total{operation}`, `realtron_whatsapp_api_errors_total{status}` (401/403/429/4xx/5xx/network), `realtron_whatsapp_token_checks_total{scope,result}`; guardrail blocks now counted. Alerts `LLMUnavailable`, `LLMProviderFailing`, `WhatsAppTokenInvalid`, `WhatsAppAuthErrors`, `AIGuardrailBlocksSpike` (promtool tests).
- **Token check** covers each active organization's own token too, treats 403 as invalid, never logs tokens, and tells admins **by email** as well as in the inbox: WhatsApp is the channel that is down.
- **Outbox sends never buffer** (KI‑113): only a Cloud API message id counts as sent; otherwise the outbox retries. Chat replies keep the buffer (KI‑114).
- **Incident tools:** `manage.py rotate_field_keys` (re-encrypts every encrypted column under the first key, in batches, counts only; refuses without a fixed `FIELD_HASH_KEY`, since the lookup hashes would change) and `manage.py revoke_sessions` (blacklists refresh tokens for chosen people or everyone).

### ADR‑053 — Deploys: one build, staging automatically, production by approval, Docker over SSH (session 30, 2026-11-25)
- **Owner decisions:** each environment is a Docker host reached over SSH; production needs approval from a required reviewer of the GitHub `production` environment. Replaces the AWS ECS and placeholder-webhook jobs (no AWS resources were defined).
- **Pipeline (`ci.yml`):** security (Bandit, `pip-audit`, TruffleHog pinned by commit) and tests (migrations check, OpenAPI, promtool, eval gate, pytest) → **build once** on `main`, images tagged with the commit, `APP_VERSION` baked in → `deploy-staging` (automatic) → `production-gate` (fails unless `production` requires a reviewer) → `deploy-production` (needs staging, the gate and the same tag). Runs on `main` are never cancelled; each environment has a non-cancelling deploy lock.
- **Deploy (`deploy.yml`, reusable):** environment-scoped secrets with the same names; host key pinned (`StrictHostKeyChecking=yes`); registry token over stdin; inputs validated before they reach the remote command. Copies only the compose file and the script; `.env` stays on the server.
- **On the server (`scripts/deploy/remote_deploy.sh`):** pull → db and Redis → migrate (stop on failure, nothing switched) → start → wait for `/health/` to report the new `version` and `ok` → record `.deployed_tag`; otherwise start the previous tag and fail. Migrations are not reversed (expand-only, ADR‑023).
- **Smoke test** after each deploy requires `ok`, the deployed version and a refused unsigned webhook.
- **Rejected:** rebuilding per environment (what runs in production would not be what was tested); `docker compose up` alone (a failing migration loops under `restart: on-failure`); a `latest` tag (no way to say what runs or to roll back).

### ADR‑054 — Visitors change or cancel viewings in the chat (KI‑076, session 31, 2026-11-26)
- **Decision:**
  - New assistant tools: `my_viewings` (read-only), and `cancel_viewing` and `reschedule_viewing`, which need confirmation (side effect `booking`, flag `feature_talk_to_agent`). They call the same `V.cancel` / `V.reschedule` services as `POST /viewings/{id}/cancel|reschedule/`, so locking, overlap checks, the visitor's limits, reminder reset and the agent's notice are the same.
  - A viewing is named by its position in `my_viewings`, its id, or the listing's reference. The confirmation summary pins the viewing id and the new UTC start, so "1" cannot mean another viewing when the user says YES. A new time must be a free slot of the same listing (`slot_at`, excluding the visit itself); the current time and invented times are refused.
  - **Scope:** only the person's own viewings that are upcoming and still open. In an organization's chat only that organization's viewings (`lead.organization`), so one agency's chat never shows a visit at another agency (as ADR‑036). On the platform number, all of them.
  - Reminders and the booking reply say "reply here" only while `feature_talk_to_agent` is on; otherwise they point to the app as before. A reminder goes from the organization's number, so a reply reaches the chat with the same scope.
- **Not done:** the visitor does not "confirm" a visit (only the agent confirms); a reply while a person handles the chat goes to that person, not the tools.
- **Rejected:** keyword handling of "CANCEL" replies outside the assistant: a bare "cancel" already means NO to a pending action, and a second path would skip the confirmation record (MVP criterion 8).

### ADR‑055 — Live-chat streams follow the contact-withdrawal rule; the embedding cache is removed (KI‑083, KI‑087, session 31, 2026-11-26)
- **Decision:**
  - The organization's agent room (`ws/agent/org/<id>/`) shows a WhatsApp number only when the session lists would (KI‑074): hidden when the person withdrew the consent on every lead they have with that organization. Then the `connected` list and `inbound_message` events carry `phone: null` and the raw message has no `from`; everything else is unchanged, so the frontend keeps working and shows "number not shared" on `null`. One helper, `apps.leads.privacy.hidden_session_phones`, one query per event or per connect.
  - The agent can still reply in the session: replies are sent by the server to the stored number, which never leaves it.
  - `SemanticResponseCache` and its embedding model are deleted, with `sentence-transformers` and `numpy`. Answer reuse stays the exact-words FAQ path (ADR‑042).
- **Rejected:** dropping the number from every stream event for everyone (the frontend uses it to label sessions, and the owner kept numbers visible for people who share them, KI‑074); keeping the cache "for retrieval" (RAG in M4 needs its own design, AI spec §7).

### ADR‑056 — Agent performance from recorded events (T‑3010, FR‑CRM‑008, session 32, 2026-11-26)
- **Decision:**
  - `GET /api/v1/reports/agent-performance/?from&to[&agent][&organization]`, computed on request from rows M2 already writes; no new tables, no stored aggregates.
  - **Enquiries:** the organization's enquiries created in the period whose lead is assigned to the agent. First reply = the first outbound message in the thread, by anyone on the organization's side. Median first-reply minutes, the share answered within one hour (of all received, so unanswered ones count against it), unanswered count.
  - **Viewings:** the agent's visits for this organization scheduled in the period, by outcome; no-show share of visits that took place (completed + no-show); visitors' own feedback (the agent's rating of the visitor is not counted).
  - **Leads:** the organization's leads assigned to the agent, created in the period; conversion = closed (`routing_state=closed`) / assigned.
  - **Period:** local days of the organization (inclusive), default the last 30, at most 366. Wall-clock times, no business hours.
  - **Access:** managers of the active organization (owner, org admin, team or sales manager) see every agent, optionally one; an agent sees only their own row, and totals cover only the rows shown; viewers and others get 403; platform admins name the organization. No phone numbers or other contact details.
- **Limits:** attribution follows the lead's current agent, so reassigning a lead moves its history; at organization scale the enquiries are read row by row (fine for thousands per period; add a materialised daily table if it grows). The older `GET /reports/agents/` and `apps/agents/stats.py` (activity-based response time) stay unchanged for the current frontend.
- **Rejected:** measuring from `LeadActivity` "contacted" rows (depends on agents logging them); attributing by message sender (unanswered enquiries would belong to nobody).

### ADR‑057 — Tasks and follow-ups on leads (T‑3020, FR‑CRM‑004, session 32, 2026-11-26)
- **Decision:**
  - New `LeadTask` (`leads 0024`, additive): lead, the lead's organization (fixed at creation), assignee (an agent), kind, title, notes, due time, reminder time (≤ due, default the due time), status open / done / cancelled. API `GET/POST /api/v1/leads/tasks/`, `GET/PATCH /api/v1/leads/tasks/{id}/`; filters `lead`, `status`, `mine`, `due=overdue|today|upcoming` (today in the organization's time).
  - **Who** (membership role, ADR‑020, not `user.role`): staff of the organization (owner, org admin, team or sales manager, CRM operator) see and change every task there and assign any active agent of it; an agent sees tasks on their own leads and tasks given to them, creates tasks only on their own leads and only for themselves, and cannot reassign; viewers and others see nothing (404). Platform admins see all.
  - **Times:** due at most a year ahead, not in the past (5-minute grace). Moving the due time keeps the reminder's lead time; moving either resets the reminder; a closed task must be reopened before it is moved.
  - **Reminder:** one notice to the assignee at the reminder time (in-app, email and WhatsApp by their `lead_updates` preference), claimed once per reminder time by a 5-minute beat job (`lead-task-reminders`). It names the task and due time, never the client's contact details.
  - Creating, completing, cancelling and reopening a task write a lead activity.
  - **Privacy:** erasure redacts the title and notes of tasks about the person (`lead_tasks_redacted`); they are internal notes, so they are not in the person's export (as appointment notes, ADR‑037).
- **Not done:** recurring tasks, automatic tasks from pipeline stages (T‑3030), calendar sync.

### ADR‑058 — Listing analytics as daily counters, no personal tracking (T‑3060, FR‑LST‑012, session 33, 2026-11-26)
- **Decision:**
  - New `ListingDailyStat` (`properties 0050`, additive): one row per listing and local day of the listing's market, with `impressions`, `detail_views`, `saves`, `enquiries`. Written by `apps.properties.analytics.record` as one `INSERT … ON CONFLICT DO UPDATE +1` per request, rows in a fixed order (no deadlocks between concurrent upserts). A failed write is logged and dropped; it never fails the page.
  - **What counts:** *impression* = a listing in a page of search results (public `GET /public/listings/search/`, `POST /search/`, the assistant's `search_listings`); *detail view* = the public detail, a consumer's `GET /properties/{id}/` on a live listing, or the assistant's `get_listing`, **once per visitor per listing per local day**; *save* = a listing newly saved (a note edit or re-save is not; dismissed → saved is); *enquiry* = a new enquiry thread (a message on the open thread is not). Saves and enquiries are counted only when their transaction commits.
  - **Not counted:** the lister's own side (owner, listing agent, members of the listing's organization), and clients that say they are bots (crawlers, link previews, `curl`, script libraries).
  - **Privacy:** no person, address or device reaches the database. The once-a-day rule uses a cache key that is an HMAC (keyed with `SECRET_KEY`) of the day, listing and visitor (user id when signed in, else address and browser string), expiring after 36 hours. Counts are not personal data: not in exports, untouched by erasure; rows go with the listing.
  - **Report:** `GET /api/v1/reports/listing-performance/?from&to[&listing][&organization][&sort][&limit][&offset]`. Per listing: the four counts, `view_rate_pct` (opened / shown), `save_rate_pct` and `enquiry_rate_pct` (of opened), with totals over every listing in scope; `listing=` adds a day-by-day series. Rows are listings with activity in the period or live now. Period: local days (default 30, at most 366), as ADR‑056.
  - **Access:** managers of the active organization (owner, org admin, team or sales manager) see all its listings; everyone sees the listings they own (private sellers), list as a freelance agent, or are assigned to in their active organization; a listing outside that is 404; platform admins name the organization or the listing.
- **Limits:** only requests that reach the application are counted. Public reads are cacheable for 60 s, so a browser re-opening within a minute is not counted, and a CDN in front of the API would hide most reads (KI‑122). Bots posing as browsers are counted up to the per-address rate limit. The day of a row is the listing's market day; the report's dates are the organization's (the same for single-market organizations).
- **Rejected:** a row per view (personal tracking, and unbounded growth); counting inside `listing_search.execute` (it also serves alerts and comparisons, which nobody sees as results); a Redis buffer flushed by beat (one more moving part; one upsert per request is cheap at today's volume, revisit with KI‑122); a client beacon (needs frontend work and is easier to inflate; the natural fix if a CDN is added).

### ADR‑059 — AI listing assistant: drafts and suggestions checked against the listing, never saved (T‑3050, FR‑LAI‑001…003, session 33, 2026-11-26)
- **Decision:**
  - Three endpoints for the people who may edit the listing (owner, listing agent, platform staff; `lifecycle.can_manage`), otherwise 404: `GET /api/v1/properties/{id}/ai/missing-info/`, `POST …/ai/describe/` `{notes}`, `POST …/ai/extract/` `{text}` (default: the listing's description). **Nothing is saved:** the person applies what they accept with the normal `PATCH /properties/{id}/`, so a human approves every change and the listing's own validation applies.
  - **Missing info (FR‑LAI‑002)**, no model: `required` = the publish rules (`publish_problems`); `recommended` = what buyers look for by category, subtype and purpose (photos, a description of 200+ characters, size, rooms, parking, completion, year, amenities, floor for flats and offices, furnishing and deposit for rentals); `optional` = market-schema attributes not set. Plus `ready_to_publish` and `completeness_pct` (share of recommended fields filled).
  - **Describe (FR‑LAI‑001):** the prompt `listing.describe` gets only the structured fields (`key: value`) and the seller's notes (contacts redacted, orders neutralised, delimited). The model must give each sentence its `sources`. Every sentence is re-checked deterministically: no source; a cited field the listing lacks; a price, size or room count that matches neither the fields nor cited notes (reuses the grounding parsers, Urdu digits included); any other number not in them; an amenity the listing does not have and no field or cited note names; contact details; superlatives and claims about schools, safety or investment returns (PRD safeguard). Marked sentences are returned with their reasons and **left out** of `description`. A title that fails the same checks is replaced by one built from the fields.
  - **Extract (FR‑LAI‑003):** the prompt `listing.extract` lists the allowed fields (rooms, parking, floors, year, pets, instalments, furnishing, completion, deposit, service charge, size, amenities, and the market's attributes, with their types). A suggestion stands only if its field is allowed, its value passes the type, range, enum and pattern rules, its `quote` is in the text word for word, and the quote states the value (the number, amount, size or amenity is in it). Values equal to the current ones are dropped. The answer includes the `PATCH` body that would apply all suggestions; the frontend sends only the confirmed ones.
  - **Controls:** flag `feature_listing_assistant` (default on; missing-info is always on), throttle `listing_assistant` (`LISTING_ASSISTANT_RATE`, default 30/hour per user), the AI kill switch (503 `ai.stopped`), 503 `ai.unavailable` when no provider answers. Calls are logged as `AIInteraction` type `listing_assist` with the prompt version (`ai 0009`, choices only). Eval suite `listing_copy` (35 cases, English, Urdu and Roman Urdu) is part of the CI gate.
- **Not done:** price extraction (the lister states the price on purpose); brochures as PDF or images; copy in other languages (FR‑LAI‑005, P2); org managers who are not the listing's owner or agent (they cannot edit it either).
- **Rejected:** checking only the numbers the model cites (it could cite `price` and state another one); letting the model mark its own unsupported sentences (the check must not depend on it); saving the draft automatically (PRD: a human always approves); blocking human-written text with the same checker on PATCH (the rule is about generated text).

### ADR‑060 — One append-only, hash-chained audit trail (T‑3070, GAP‑104, session 33, 2026-11-26)
- **Context:** three overlapping logs: `core.AuditLog` (admin, membership, kill switch, MFA, moderation, escrow), `compliance.PrivacyAuditLog` (erasure, export, consent) and `security.ApiSecurityEvent` (security events; HMAC-chained, but the next link is read without a lock, so concurrent writers can fork the chain and verification then fails). Only the last was tamper-evident; none was protected in the database.
- **Decision:**
  - New `core.AuditEvent` (`audit_events`, `core 0004`): `seq` (gap-free), `at`, `category` (admin, organization, privacy, security, ai, listing), `action` code, `actor_id`, `organization_id`, `target_type`/`target_id` (ids, **no foreign keys**, so deleting a person never rewrites a row), `meta` (non-personal), `personal` (addresses, browser, free text, before/after), `personal_digest`, `prev_hash`, `record_hash`.
  - **Chain:** `record_hash` = HMAC-SHA256 (key `AUDIT_CHAIN_KEY`, default `SECRET_KEY`) over the fixed fields, `meta`, the personal digest and `prev_hash`. Writers take `pg_advisory_xact_lock` and append `seq + 1`, so the chain is linear under concurrency (tested with six threads). An event rolls back with the change it records.
  - **Append-only in the database:** a trigger refuses every DELETE and every UPDATE, except emptying `personal` with `redacted_at` set (erasure). Someone who can disable the trigger can still not hide a change: `verify` reports the first row whose hash, link, sequence or personal digest does not fit.
  - **Erasure:** step `audit_events_redacted` empties `personal` on rows by, about or mentioning the person (actor, target user, phone, email or id in the text). What happened, when and to which record stays, and the chain still verifies.
  - **One writer:** `apps.core.audit.record`. **Expand phase:** the three older logs are mirrored on write (post-save, created rows only), so every existing call site lands in the trail without changes; a failed mirror is logged (`audit.write_failed`, metric `realtron_audit_write_failures_total`) and never fails the original action. `AuditLog.log` passes the target's organization.
  - **Read:** `GET /api/v1/audit-events/` (filters category, action, actor, target, UTC days; newest first). Platform admins see all; the owner and admins of an organization see its events, without security events and without addresses or browser strings. `GET /api/v1/audit-events/verify/` (platform admins), `manage.py verify_audit_chain` (exit 1 when broken), daily beat `verify-audit-chain` that logs the head (`seq`, hash) outside the database and sets `realtron_audit_chain_ok`.
- **Not done (KI‑123):** moving the old readers (`/audit-log/`, `/compliance/privacy-audit/`, `/security/…`) to the trail and dropping the old tables (contract phase, like T‑1070g); an alert rule on `realtron_audit_chain_ok`; retention (rows are kept; a cut-off would anchor verification at the oldest kept row). `audit.SecurityTrace` and `ComplianceViolation` stay AI guard data (per-message, already redacted by erasure), not audit events.
- **Rejected:** a row hash over the personal values themselves (erasure would break the chain or keep the data); sealing rows later in a batch job (a window in which rows are not protected); foreign keys to users (SET NULL on deletion would change hashed rows); a separate trail per log (the gap is that there are several).

### ADR‑061 — AI ops console as read models over what the runtime records (T‑3090, GAP‑103, session 33, 2026-11-26)
- **Context:** operators had a cost report (`/ai/costs/`), token usage and kill switches, but no way to see which prompt versions run, which providers are configured or tripped, what models cost, which tools are on, how evals stand, or individual turns, without the Django admin.
- **Decision:** endpoints under `/api/v1/ai/ops/`, for platform `ai_admin`, `ops_admin` and `super_admin`; **only AI admins (and super admins) change anything**.
  - `overview/`: turns by route, model, prompt; grounding results; errors; latency p50/p95/max of model turns; priced cost by currency; active kill switches. UTC days, at most 90.
  - `prompts/` and `prompts/<name>/`: the code registry (name, version, digest, variables, template) and the versions seen in `AIInteraction`, marked current or not. **Prompts stay in code** (reviewed, pinned in `tests/test_llm.py`, gated by evals); the console shows them and does not edit them.
  - `models/`: providers in failover order with configured, circuit-breaker state, model, today's `ModelPrice` and usage. `model-prices/`: list and add; a change is a new row from its day (409 on the same model and day), never an edit.
  - `tools/`: the registry with side effect, confirmation rule, flag state, markets, parameters, and calls by status from `AITrace.tool_calls`.
  - `evals/`: start a run of the deterministic suites (all or chosen), stored as `AIEvalRun` (`ai 0010`) with per-language results and at most 20 failures per suite; list and detail. **Live (paid) runs stay on the command line.**
  - `traces/` and `traces/<id>/`: turns with filters (organization, route, prompt, model, provider, grounding, error, conversation reference, fallback); no user and no message text (traces hold none).
  - Price additions and eval runs are written to the audit trail (ADR‑060, category `ai`). `AIEvalRun.started_by` is a user id without a foreign key, like the audit trail.
- **Not done:** editable routing config in the database (AI spec §3.3; provider order stays `AI_PROVIDER_ORDER`); prompt editing; a background job for long eval runs (offline suites take seconds).
- **Rejected:** prompts stored and edited in the database (bypasses review and the eval gate); a serializer-per-view schema for these read models (the frontend contract is the tests and descriptions, like the cost report).

### ADR‑062 — Pipeline stages per organization, beside the AI's lead status (T‑3030, FR‑CRM‑002, session 33, 2026-11-26)
- **Context:** FR‑CRM‑002 asks for configurable stages (default new → contacted → qualified → viewing → offer → won/lost) "replacing" the fixed `Lead.Status`. In the code `Lead.Status` (new, warm, qualified, cold, blocked_malicious) is the **AI's qualification**: scoring, the guardrails, cold-lead jobs, enquiries, re-engagement campaigns, the AI cost report and performance reports read and write it. Replacing it would change all of them and mix two meanings.
- **Decision:**
  - New `PipelineStage` (`lead_pipeline_stages`, `leads 0025`): organization, `key` (slug, unique per organization, never changes), `name`, `kind` (open, won, lost), `position`, `archived_at`. The defaults are created on first use (no backfill); at most 20 active stages.
  - `Lead.stage` (FK, PROTECT) and `stage_changed_at`. **A lead without a stage is in the organization's first open stage**, so every existing and every new lead has a stage without a data migration; a stage from another organization (the lead was rerouted) counts the same. Reordering pins unstaged leads to the old first stage so they do not move.
  - `Lead.status` keeps its meaning (the AI's qualification) and is untouched by stage moves. `routing_state` is kept: moving to a **won or lost** stage sets `closed` (the existing closed-lead metrics now have a writer); moving back to an open stage reopens it (`agent_assigned` with an agent, else `org_queue`).
  - **Permissions:** the stages are read by every member of the organization; owner and org admin add, rename, change kind (refused while leads are in it or if it is the last open stage), reorder and archive (its leads move to a chosen stage). Organization staff move any lead; an agent moves the leads assigned to them; platform admins name the organization. Each move is a `LeadActivity` (`stage`, from/to, routing change, note); stage changes go to the audit trail (`pipeline.*`).
  - **API:** `/api/v1/leads/pipeline/stages/` (+ `reorder/`, `<id>/`, `<id>/archive/`), `/pipeline/board/` (stages with counts and the first leads per stage, as far as the caller may see them; cards carry no phone), `POST /api/v1/leads/<id>/move-stage/`, and `stage` on the lead itself.
- **Not done:** rules that move leads automatically (e.g. a viewing booked → `viewing`; T‑3040 and the follow-up automation can use `pipeline.move`); stage reports (time in stage, conversion between stages) for T‑3110; a contract step that retires `Lead.Status.QUALIFIED` as a CRM column in the frontend.
- **Rejected:** renaming `Lead.Status` values into stages (two meanings in one field, and every AI reader changes); a stage history table (the `LeadActivity` timeline already records each move); a data migration that stamps every lead (the "null = first stage" rule needs none and survives rerouting).

### ADR‑063 — Team assignment rules per organization, extending auto-assign (T‑3040, FR‑CRM‑007, session 34, 2026-11-26)
- **Context:** auto-assign (`suggest_agents_for_lead`, the 15-minute `auto_assign_unassigned_leads` job, `POST /leads/<id>/auto-assign/`) used one fixed score: rating, featured, verified, primary city, minus 0.5 per assigned lead, with the city as a hard filter and specialization as a soft one. Organizations could not choose round robin, cap an agent, pause one, or route by area or language. Manual paths had gaps: `bulk-assign` let a developer give leads to **any** active agent (another organization's too) with a bare `UPDATE` (no activity row, no notification, `routing_state` unchanged), and no assign path set `routing_state` to `agent_assigned`.
- **Decision:**
  - `AssignmentRules` (`lead_assignment_rules`, one per organization, `leads 0026`): `strategy` (`best_match` = the old score, `round_robin`, `least_loaded`), `match_city` / `match_area` / `match_language` / `match_specialization` each `off`, `prefer` (keep the matching agents, everyone if none match) or `require` (none match → the lead stays in the queue), `only_available` (skip busy/offline), `max_open_leads` (cap per agent), and the round-robin cursor `last_agent`. **No row = the old behaviour** (best match, city required, specialization preferred, no cap), so nothing changes until an organization sets rules.
  - `AgentRouting` (`lead_agent_routing`, per organization and agent): `accepts_leads` (pause) and an own `max_open_leads` that overrides the organization's cap.
  - **Who may get a lead** (`assignment.org_agents`): the organization's active agents and active freelance collaborators with `can_receive_leads` (the T‑1032b rule), minus paused agents, agents at their cap and, with `only_available`, busy or offline ones. **Open leads** = assigned leads of that organization whose routing is not closed and that are not blocked.
  - **What a lead asks for:** city = `city_interest`; area = locations of the listings it enquired about (agent `areas` matched by containment, at least 3 characters); language = the language of the person's latest assistant conversation (`en`, `ur`, `roman`, `ar` mapped to the names agents write, e.g. "Urdu"); specialization = the intent map. A lead with no value for a match skips it.
  - **Round robin** goes in joining order to the agent after `last_agent`; `auto_assign` locks the lead and the rules row, so concurrent runs in one organization do not hand two leads to the same turn.
  - `assign_agent_to_lead` now sets `routing_state = agent_assigned` (unless closed), counts `Agent.total_leads` / `last_active_at`, and records the rule (strategy, matched rules, open leads, cap) on the `ASSIGNED` activity.
  - Manual `assign` and `bulk-assign` only take an agent who works for the lead's organization (platform admins included; unscoped leads go to any active agent). Bulk assign: at most 200 ids, per-lead activity and routing state, one notification for the batch.
  - **API:** `GET/PATCH /api/v1/leads/assignment/rules/`, `GET /assignment/team/` (agents with open leads, cap, paused, availability, cities, areas, languages, specializations, whether they receive leads now; no phones), `PATCH /assignment/team/<agent_id>/`. Organization staff read; owner, org admin and **team manager** change (team management is their role). Platform admins name the organization. Changes go to the audit trail (`assignment.rules_changed`, `assignment.agent_routing_changed`). `suggest-agents` items gain an additive `assignment` explanation.
- **Not done:** working hours / shifts per agent (availability is the agent's own status); weighted round robin; rules per team inside an organization (there is no team entity below the organization); re-assignment of leads an agent leaves unanswered (the SLA part of FR‑COM; a follow-up can use `auto_assign(reassign=True)`); stage moves from assignment.
- **Rejected:** a generic rule engine with ordered conditions (more than FR‑CRM‑007 asks and hard to explain to agencies); storing language on the lead (the conversation already has it and changes with it); counting load across organizations for freelancers (each organization caps its own work).

### ADR‑064 — Unified contact and messages: one person per organization, one read view over two logs (T‑3100, FR‑CRM‑001, GAP‑016, FR‑COM‑009, session 34, 2026-11-26)
- **Context (inventory):**
  - *Person:* the real person is `Lead.user`; one person can have several leads (requirements) in one organization (enquiries, WhatsApp, manual). `CRMContact` (organization + plaintext phone + tags) had **no writer** apart from erasure; `ClientProfile` had **no reader or writer**.
  - *Messages:* WhatsApp is logged in `WhatsAppMessage` (per session; inbound and the assistant's replies, from `router._log_inbound` / `_send_and_log`) and **mirrored** into `ConversationMessage` only when the person already has a lead in that organization. The agent live-chat consumer writes only `WhatsAppMessage`; dashboard `send-message` writes only `ConversationMessage`; enquiry threads and dashboard notes live only in `ConversationMessage`. So the lead's `/conversations/`, the AI summary and suggested replies missed parts of the chat, and old mirrors may sit under another organization's lead (KI‑073).
- **Migration plan (expand → switch writers → contract):**
  - **Phase A — done here (expand, read side):**
    - SQL view `unified_messages` (`leads 0027`, unmanaged model `UnifiedMessage`): both logs as one list with `source`, `channel`, `direction`, `msg_type`, `body`, `has_media`, original ids (`row_id`, `lead_id`, `session_id`, `enquiry_id`, `organization_id`, `sender_id`). A WhatsApp row mirrored into a CRM row **of the same organization** shows once (as the CRM row). A CRM mirror of **another organization's** WhatsApp message is left out (KI‑073 for every reader of the view). Ids are plain columns, so callers filter on `row_id`/`session_id`, which use the base tables' indexes.
    - Who sees what is unchanged and decided in `apps/leads/contacts.py`: CRM rows through `Lead.crm_messages()` (never another lister's thread); WhatsApp rows of the person's sessions with the same organization's number (unlinked sessions matched by phone, as erasure does). Agents see the people whose leads are assigned to them and those leads' CRM rows.
    - **Contact = (organization, person)**, read from the leads; no contact row to keep in step. `CRMContact` is repurposed as the organization's notes on a person (`user` FK, own name, tags; written only on PATCH; new rows store **no phone**); old phone-only rows linked to the registered person by `leads 0027`. `ClientProfile` marked deprecated.
    - **API:** `GET /api/v1/leads/contacts/` (search by name, contact name or ≥4 digits of the phone, `tag`; people who withheld their number are listed without it and are never found by phone), `GET|PATCH contacts/<user_id>/`, `GET contacts/<user_id>/messages/` and `GET /leads/<id>/messages/` (newest first, opaque `before` cursor, ≤100). `/leads/<id>/conversations/` kept, deprecated. Summaries and suggested replies read the unified list.
    - Privacy: erasure deletes the notes by person; export gains `agency_contact_notes`; the view holds no data of its own.
  - **Phase B — next (T‑3100b): one writer table.** New table `messages` (channel, direction, author, body, media, wa id unique per channel, lead nullable, session nullable, enquiry nullable, organization). Every writer (router, consumer, `send-message`, enquiry `post`, campaigns) writes it once; the WhatsApp→CRM mirror stops; the view becomes a `UNION` of `messages` and the two old tables for rows before the switch; a batched backfill copies old rows, after which the view reads `messages` only. `MessageAttachment` moves to the new id. Copilot and AI history read it too.
  - **Phase C — contract (T‑3100c), one release after B is rolled out:** drop `ConversationMessage`, `WhatsAppMessage` (or keep the raw payloads in a short-retention debug table), `ClientProfile` and `CRMContact.phone`, behind a gate like `check_listing_contract`.
- **Operational note:** Postgres refuses `ALTER COLUMN … TYPE` and `DROP COLUMN` on columns a view uses. A migration that changes those columns of `conversation_messages`, `whatsapp_messages`, `whatsapp_sessions`, `leads` or `enquiries` must drop and re-create `unified_messages` around it.
- **Not done:** web assistant conversations (`apps.conversations`) stay out of the organization's view (ADR‑033 kept organization access out; owner decision if wanted); merging duplicate *people* (two accounts of one human) — leads are merged by `MergeLeadsView`; contact import.
- **Rejected:** a materialised contact table filled by signals (every lead writer would have to keep it in step); one big-bang message migration (too many writers to switch safely in one release); copying the phone into `CRMContact` (a second copy of personal data to erase).
- **Addendum (T‑3100b, phase B, session 34):** the one writer table is **`conversation_messages` itself**, not a new `messages` table: it already holds the CRM log, the enquiry threads and their attachments (`MessageAttachment` FK), so extending it moves the fewest readers. Added `session` (WhatsApp rows), `organization` (the number's organization), `msg_type`, `media_id`, `media_url`; `lead` is now optional (a WhatsApp message before the person has a lead here); unique `(session, wa_message_id)` for non-empty ids, which old rows (no session) never hit (`leads 0028`).
  - **Writers:** `apps/whatsapp/history.record` is the only WhatsApp writer: router inbound (redelivery updates the row) and assistant replies, the agent live-chat consumer (sender = the agent), dashboard `send-message` (in the person's session with the organization's number). `WhatsAppMessage` is no longer written and the WhatsApp→CRM mirror is gone. Sends Meta gave no id for get a unique fallback id (the consumer used one fixed id per session). Raw webhook payloads are no longer stored.
  - **Readers** go through the view by session (`history.session_log`), so old and new rows are seen without a gap: the 24-hour window check, the copilot, the live-chat history, the bot report, the re-engagement transcript (now only the lead's own organization's chat; it read every session with the phone), the dataset curator, export and erasure (log rows deleted by session, media at Cloudinary deleted).
  - **View v2:** an old mirror (no session) is hidden when the WhatsApp row exists, which shows instead with its session and media (covers KI‑073 too); a WhatsApp row copied by the backfill shows once, as the copy.
  - **Backfill:** `manage.py backfill_contact_messages` (batched, idempotent; `--check` exits 1 while rows remain): a same-organization mirror is completed in place (keeps its lead and id), anything else is copied with its original time and no lead; KI‑073 mirrors are left hidden. It is the gate for phase C.
  - **Phase C (T‑3100c)** after the backfill reports `pending=0` in production: view reads `conversation_messages` only (the KI‑073 rule then needs another source: delete those mirrors with the owner, or keep a list), drop `WhatsAppMessage`, `ClientProfile`, `CRMContact.phone`.

### ADR‑065 — Investment calculator: deterministic numbers, sourced assumptions, commentary apart (T‑4010, FR‑INV‑001…004, session 34, 2026-11-26)
- **Decision:** all figures come from `apps/properties/investment.py` (Decimal arithmetic, whole currency units, one currency, no FX). Each figure carries its formula; each assumption its source: `user`, `listing`, `platform_median` (median monthly asking rent of published, live, long-term rent listings in the same country, city, currency, category, subtype, bedrooms and ±25% size; shown with N, quartiles and date; not used below `INVESTMENT_MIN_COMPARABLES`, default 5), `market_calculator` (purchase fees = transfer tax + stamp duty, yearly tax, from the existing `FinancialEngine`, asked as an investor buying another property), `platform_default` (vacancy 8.33%, maintenance 5%, management 0%, 5 years).
- **No predictions:** the platform assumes no growth; appreciation scenarios (≤3) are only the user's own rates. Cash purchase unless the user gives a down payment, rate and tenure. Selling costs are not included and say so; PK 236K advance tax is noted as not included.
- **Commentary** (`apps/ai/investment_commentary.py`, prompt `investment.commentary` v1) explains the result and never computes; each paragraph is re-checked: any number not in the result (±0.1%, small integers ≤12 allowed for wording) or advice/promise wording drops the paragraph (`left_out` lists why). Labelled "General information, not financial advice." and returned beside the numbers, never inside them. Model unavailable → 503 with the numbers still in the body.
- **Access:** the calculator is public (`AllowAny`, scoped throttle `investment` 60/min) and reads only public listings, or a listing the caller manages (`can_manage`). Commentary needs sign-in, the flag `feature_investment_commentary` (default **off** until legal signs off the disclaimer, PRD Phase 4 criterion), and the `investment_commentary` throttle (20/hour); calls are logged as `AIInteraction` type `investment_commentary` (`ai 0011`).
- **Rejected:** model-computed figures (unverifiable); rent estimates from sale prices or a fixed yield (would be an undisclosed valuation); a stored calculation model (nothing needs it yet; responses are reproducible from the inputs).

### ADR‑066 — Indicative price context from asking prices, not a valuation (T‑4020, FR‑VAL‑001…004, session 34, 2026-11-26)
- **Decision:** `apps/properties/price_context.py` shows where asking prices per m² of comparable listings lie: published, live (`visible_to(None)`) sale listings with a total price and a size, same country, currency, category and subtype (when given), size within ±30%; the listing's **locality** (its sub-localities included) when it has ≥ `PRICE_CONTEXT_MIN_SAMPLE` (default 8), else the city; the listing itself excluded. Up to 1,000 rows.
- **Shown:** N, scope and place, p25/median/p75 (linear interpolation), min/max, spread `(p75 − p25) ÷ median`, data age (median and oldest days listed), confidence `high`/`medium`/`low` with reasons (`few_comparables` < 20, `moderate_spread` > 0.35, `wide_spread` > 0.6, `older_listings` > 180 days; medium needs ≥ 12 and one non-spread reason at most). Every figure carries provenance `listing_asking_price`; a market-named disclaimer ("not a valuation or appraisal … asking prices, not sale prices … consult a licensed valuer").
- **Subject:** its asking price per m², percentile and `below/within/above_middle_half` — never "over/under-priced", no estimated value. Below the minimum sample: `available: false` with the reason and N (FR‑VAL‑001 hides it).
- **Audit redesign (first step):** `AuditEngine.run` adds `listing_comparables` (PK pack only, KI‑045) beside the benchmark figures, never mixed into them, and the PDF prints it as its own labelled table. The benchmark scoring and its own provenance stay as they were; replacing the hardcoded benchmarks with these figures needs the owner and legal (valuation-claim liability, PRD Phase 4 risk).
- **Access:** `GET /api/v1/price-context/` public (throttle `price_context`, 60/min) by criteria or a listing that is public or managed by the caller (same lookup as T‑4010).
- **Rejected:** a model-made estimate; hedonic regression on asking prices (looks like a valuation, too little data per locality); including rent listings (other price basis).

### ADR‑067 — Comparison narrative constrained to cited table cells; running costs as labelled estimates (T‑4030, FR‑CMP‑003/004, session 34, 2026-11-26)
- **Running costs (FR‑CMP‑004):** `comparison.running_costs` adds to each priced sale listing in a market with a calculator: purchase fees (`once`), yearly property tax (`year`) — both `market_calculator`, the investor basis of ADR‑065 — the listing's yearly service charge (`listing`) and their yearly sum, converted like the prices, with `estimate: true`, up to three calculator notes and a label ("estimates … not quotes"). Rent listings, hidden prices and markets without a calculator get `null`.
- **Narrative (FR‑CMP‑003):** the model sees the comparison as cells `LETTER.row: value` (20 rows, "not specified" for unknowns) and must cite the cells of each sentence. A sentence is left out when it cites none or an unknown cell, states a number that is neither a cited cell's value (amount or converted, 1% or 0.5 tolerance; crore/lakh/million/k understood) nor the difference of two cited cells of one row (small counts ≤ 4 allowed), or uses judgement/advice wording (best, bargain, over/under-priced, worth, invest/returns, schools, safety, recommend …). `left_out` gives only problem codes. Unknowns (rows some listings specify and others do not) are computed in code, never by the model. Letters map to listing ids in `listings`.
- **Access:** `GET /api/v1/compare/narrative/?ids=…[&display_currency=]` signed in (same visibility as `compare/`), flag `feature_comparison_narrative` (default **on**: facts only, like the listing assistant), throttle `comparison_narrative` 30/hour; `AIInteraction` type `comparison_narrative` (`ai 0012`). Model unavailable → 503 with the comparison still in the body.
- **Rejected:** letting the model pick a "winner" (the code already gives `best` per row only when unambiguous); free text without citations (unverifiable).

### ADR‑068 — Offers register: immutable offers, counter chains, no money and no listing changes (T‑4060, FR‑CRM‑009, session 34, 2026-11-26)
- **Model:** `LeadOffer` (`lead_offers`, `leads 0029`) on a lead of an organization: optional listing (must be the organization's own), `party` (`client` = the lead, `counterparty` = owner/landlord side), whole `amount` > 0, `currency`, `basis` (total / per month / per year), `conditions` (≤1,000), optional `expires_at` (≤ 1 year), `status`, `decided_at`, `decision_note`, `created_by`. Offers are never edited: a change of terms is a counter-offer — a new row with `previous` (one-to-one), from the other party, same listing, currency and basis — and the answered offer becomes `countered`.
- **Lifecycle:** only `open` changes: `accepted`, `rejected`, `withdrawn` (`POST …/decide/`), `countered`, or `expired` (on read and by beat `lead-offer-expiry`, every 15 min). A partial unique constraint allows one accepted offer per lead and listing (409 `offer.already_accepted`); deciding a non-open offer is 409 `offer.not_open`. Expiry is written outside the decision's transaction so a refusal does not undo it.
- **Who:** the tasks rule (ADR‑057): organization staff on every lead there, agents on leads assigned to them, platform admins everywhere; others get 404.
- **Side effects:** a `LeadActivity` (`offer`) per event and an organization audit event (`lead.offer_recorded|accepted|rejected|withdrawn`). Recording an offer moves the lead **forward** into the organization's `offer` stage when it is in an earlier open stage (never backward, never out of won/lost); accepting moves nothing — the deal is closed by the stage move the team makes. The listing's status (`under_offer`, sold) is not changed: that stays a listing-lifecycle action with its own checks. No payment, deposit or escrow (deal lock stays frozen).
- **Privacy:** erasure keeps amounts and dates (agency records) and redacts `conditions` and the decision note; the person's export lists their offers.

### ADR‑069 — Persistent duplicate clusters from a fingerprint and photo hashes; fraud signals as moderation flags (T‑4050, FR‑LST‑010, session 34, 2026-11-26)
- **Fingerprint:** `Property.duplicate_key` = hash of country, place cell (public snapped coordinates to 0.001°, else the location node, else normalised city + location text), purpose, category, bedrooms, currency, price type — written synchronously on save (read back after the DB trigger). Two listings match on it when size (m²) and price are each within 5% (or both price on request). Buckets were rejected: neighbours across a bucket edge would be missed.
- **Photos:** `PropertyImage.dhash` (64-bit difference hash of the sanitised pixels, computed at upload; `rebuild_duplicate_clusters` fills old photos). A shared photo — same sha256 or dHash within 4 bits — in the same country links two listings whatever their text.
- **Clusters:** `DuplicateCluster` (`listing_duplicate_clusters`, `properties 0051`), `Property.duplicate_cluster`. After a listing or its photos change, a task (on commit) re-clusters it: joins the cluster of any linked listing, merges clusters (a confirmed one survives), dissolves clusters of one. Nightly `duplicate-clusters-rebuild` re-checks everything. Archived listings leave; suspended ones stay (they are the evidence for reposts). Canonical = oldest published, unless a moderator chose one.
- **Moderators** (`/api/v1/moderation/duplicates/`, platform moderation roles): list (largest first; filter by status or evidence), confirm with an optional canonical, split a listing out (`duplicate_distinct`: never clustered automatically again). Both audited (`listing.duplicates_confirmed`, `listing.duplicate_split`).
- **Fraud signals v2** (flags in `moderation.checks.run`, so they route a listing to review; never an automatic block): `image_reused` — a photo also on another lister's listing (same lister reusing photos is not flagged); `reposted_after_takedown` — linked by fingerprint or photo to a listing that is suspended or was rejected/suspended in moderation. The exact `possible_duplicate` check is unchanged.
- **Not done:** collapsing clusters in consumer search (needs a ranking decision on which copy to show and a frontend "also listed by"); cross-country photo matching.

### ADR‑070 — Duplicate clusters collapsed in consumer search (T‑4051, FR‑LST‑010, MVP "de‑duplicated matches", session 35, 2026-11-27)
- **Context:** ADR‑069 built clusters but left search showing every copy ("needs a ranking decision"). The MVP promise (MVP_SCOPE §1, PRD persona table) is de‑duplicated matches. Decided from those documents; the choice is reversible by setting.
- **Shown copy:** a confirmed cluster's canonical listing when it matches the search; otherwise the copy that ranks first for this query and sort (relevance, newest or price). Ranking itself is unchanged.
- **Nothing is hidden:** the other copies that also match and are visible to the caller are attached to the shown hit as `also_listed` (at most 5: id, ref no, slug, price — `null` when on request — currency, price type, lister type, organization name for organization listings only, verified) and `also_listed_count`. A copy the caller cannot see, or that fails the filters, takes no part, so it never hides a visible one. Copies are ordered by price, then age; copies with the price on request come last by age, so their position never reveals the hidden price.
- **Paging:** one `ROW_NUMBER()` per group (`COALESCE(duplicate_cluster_id, id)`) in a subquery, before the cursor condition, so a hidden copy never reappears on a later page. `total` counts properties; facets still count listings. Cursors are bound to collapsed vs full results.
- **Controls:** `SEARCH_COLLAPSE_DUPLICATES` (default on); `collapse_duplicates` (bool) on `POST /search/` and the public `GET` search.
- **AI tool:** `search_listings` gets only `also_listed_count` (and a note); other copies' prices are not handed to the model, so the grounding checker's listing set is unchanged.
- **Not done:** saved‑search alerts still announce a repost of an already announced property (they use `filtered` directly); the listing detail page does not show its copies; the costs of the window are one extra scoring pass, acceptable at launch volumes (re-measure with the 200k-row set if search latency SLOs move).
- **Addendum (session 35, KI‑126):** saved-search alerts follow the same rule: a new listing is not announced when a listing of its cluster was already announced to that search, or saved or dismissed by the person; copies found in one run are announced once (the newest). Uses the existing `AlertDelivery` rows; no migration.

### ADR‑017 addendum — `ApiError` for refusals with specific codes (T‑1092, KI‑038, session 35, 2026-11-27)
- Views raise `apps.core.errors.ApiError(message, code, status)` instead of returning `Response({'error': …}, status=4xx)`. The body keeps `error` (same string) and gains `detail` plus the envelope with the given code (namespaced: `verification.*`, `auth.*`, `users.*`, `leads.*`, `listing.*`, `compare.*`, `audit.*`). Additive for clients.
- **Not an authentication failure:** `security_exception_handler` does not feed `ApiError` 401/403 into the consecutive-failure IP blocker. The hand-built responses never reached it; counting them now would let ordinary refusals (a client opening an agent page) block an address.
- Responses that must be modified (token refresh clears cookies) stay Responses and carry a `code` key, which `ErrorEnvelopeMiddleware` already honours. A guard test refuses any new `{'error'}` response without a code.

### ADR‑016 addendum — Schema descriptions for hand-built `APIView`s (T‑1091, KI‑037, session 35, 2026-11-27)
- Each app keeps the descriptions in `apps/<app>/openapi.py` (prebuilt `extend_schema` decorators, `inline_serializer` shapes) and the views apply them as `@API.<name>`; the views stay readable and the shapes are next to each other for review. Describe what the view really returns: the serializers it uses where it uses one, inline shapes for fixed dicts, a generic object only where the body is free-form.
- Choice sets that already exist under another enum name are written as strings with the values in `help_text`, so no existing enum component is renamed.
- Provider callbacks (identity providers; later payment/WhatsApp webhooks) are `exclude=True`: clients never call them and their contract is the provider's.
- Two views on one class for a collection and an item are split into two classes (`http_method_names`), which also removes the 500 for a method without its `pk`.
- Batch 3: viewsets whose `get_queryset` needs a signed-in user return `Model.objects.none()` under `swagger_fake_view` (the existing pattern), so path parameters are typed. Choice sets that only had hash-suffixed or field-derived names get an `ENUM_NAME_OVERRIDES` entry (component names change once; nothing in the backend or `pakpropaiweb` referred to the old ones). With every view described, the ratchet budgets are 0: a new view without a description fails the schema test.
- Batch 2: a description stacked over an existing `extend_schema(parameters=[IDEMPOTENCY_PARAMETER])` adds to it (drf-spectacular concatenates parameters), so idempotent endpoints keep the header. Where the serializers live in the module that imports `openapi.py` (`external_api.py`, `provisioning_views.py`), the descriptions are small functions that import lazily. A webhook view with both `GET` (verification handshake) and `POST` is excluded at class level.

### ADR‑071 — Database backups and restore drills on the Docker host (T‑6010, MVP §7, TR‑17, session 35, 2026-11-27)
- **Context:** production Postgres is the `db` container on a local Docker volume (ADR‑053). Nothing backed it up; the launch checklist requires a completed restore drill, TR‑17 backups with tested restore.
- **Decision:** host-side scripts in `scripts/ops/`, shipped by the deploy job and run by systemd timers (`ops/systemd/`):
  - `backup.sh`: `pg_dump --format=custom` **inside the `db` container** (exactly the server's version, its own credentials) piped into `gpg --symmetric --cipher-algo AES256` with a passphrase file (mode 600/400 enforced). No plaintext dump touches disk. The archive is then decrypted and read with `pg_restore --list` before it counts; `.sha256` beside it; off-host copy through an operator command (`BACKUP_UPLOAD_CMD`: rclone, aws s3, scp — no provider SDK in the app); local retention `BACKUP_KEEP_DAYS` (14).
  - `restore_drill.sh`: checksum, a throwaway container from the live db's image with **no network**, `pg_restore --exit-on-error`, core tables and migrations present, row counts and duration reported (measured RTO for the data size). The live database is never a restore target.
  - Every run is a `BackupRun` row (`core 0005`, metadata only) via `manage.py record_backup_run`; `apps.core.backups.BackupStatusCollector` exposes the latest success/failure per kind on `/metrics/` (0 = never, so "never" alerts like "stale"). Alerts: `BackupMissing` (26 h, critical), `BackupFailed`, `RestoreDrillOverdue` (35 days), `BackupStatusAbsent`.
- **Why not in the app images:** the web/worker images carry `libpq` only; a `pg_dump` from Debian would not match the server's major version. Why `gpg`: a standard, auditable tool any operator can use to decrypt, instead of a custom format.
- **Not decided here (owner):** the off-host destination (possibly paid), key custody outside the server, RPO/RTO. Daily dumps give RPO ≤ 24 h; less needs WAL archiving (T‑6050). A managed database would replace this with the provider's backups plus the same drill.
- **Restore of production:** documented in `docs/runbooks/backups.md` as a reversible swap (restore into a new database, rename, keep the old one).

