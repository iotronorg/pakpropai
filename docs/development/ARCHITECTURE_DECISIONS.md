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
