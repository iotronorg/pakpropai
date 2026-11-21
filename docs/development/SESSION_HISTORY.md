# Session History

## Session 1 — 2026-11-21 — Product assessment & PRD
- Created 11 documents in `docs/product/ai-real-estate-agent/`. No source changes.
- Tests not executed: the host had no Python 3.12 and no Django.

## Session 2 — 2026-11-21 — M0 stabilisation, first pass
- **Objective:** set up tracking and a test baseline, then fix M0 P0 issues.
- **Completed:** T‑0002, T‑0004, T‑0005, T‑0006, T‑0007, T‑0008, T‑0012; partial T‑0003 and T‑0015.
- **Tests:** baseline 1361 passed, 1 skipped, 1 failed → end 1425 passed, 1 skipped, 0 failed (executed in Docker).
- **Found:**
  - `list_property` constraint bug (KI‑021): WhatsApp self‑listing never worked.
  - Test cache leakage (KI‑022).
  - The assessment wrongly called `use_membership_rbac` unused (corrected in KI‑010).
- **Not committed.**

### Handoff → session 3
1. Start with `git status`. All session‑2 work is uncommitted on `development`; do not discard it.
2. Run `scripts/dev/test.sh`. Expect 1425 passed, 1 skipped.
3. Next: T‑0009 (wire `ComplianceEngine` from `apps/ai/compliance_filters.py` into `AIServiceManager.process` after the output guard, then add tests). Then T‑0017 and T‑0013.
4. Open owner questions: see DEVELOPMENT_STATUS → Blockers.

## Session 3 — 2026-11-21 — M0 completion
- **Owner instruction:** no commits; all other decisions approved.
- **Completed:** T‑0003, T‑0009, T‑0010, T‑0011, T‑0013, T‑0014, T‑0015, T‑0016, T‑0017. All M0 tasks are now done in code.
- **Tests:** 1452 passed, 1 skipped, 0 failed. The migration rehearsal passed forward/reverse.
- **Found and fixed:** WhatsApp audits always failed (KI‑026); non‑constant‑time token compare (KI‑027).

### Handoff → session 4
1. `git status`: everything is uncommitted on `development`, including 7 deleted artifact files. **Do not commit** (owner rule).
2. `scripts/dev/test.sh` → expect 1452 passed, 1 skipped.
3. M0 exit is pending only a GitHub Actions run (owner pushes). Start M1: create `milestones/MILESTONE_01_CORE_FOUNDATION.md` from PRD Phase 1, then T‑1010 OTP hashing.

## Session 4 — 2026-11-21 — M1 start
- **Focus:** the pasted prompt had no focus filled in, so the tracker's next task was taken (M1 kickoff → T‑1010, T‑1020).
- **Start state:** owner had committed sessions 1–3 (`d283324`); tree clean; baseline 1452/1/0.
- **Completed:** M1 milestone file; T‑1010 OTP hashing (ADR‑011); T‑1020 inbound WhatsApp retry/dead‑letter (ADR‑012). The outbound outbox was split out as T‑1021.
- **Tests:** 1477 passed, 1 skipped, 0 failed; no migration drift; Bandit 0 HIGH.
- **Found:**
  - Under DB errors the inbound task fell back to `org=None` (wrong tenant credentials).
  - An enqueue failure left the idempotency key set, so Meta's retry was dropped.
  - The OTP admin listed plaintext codes.
  - New issues logged: KI‑029/030/031.
- **Not committed.**

### Handoff → session 5
1. `git status`: session‑4 changes are uncommitted. **Do not commit.**
2. `scripts/dev/test.sh` → expect 1477 passed, 1 skipped.
3. Next: **T‑1030**. Write the membership consolidation ADR: inventory `OrganizationMembership`, `AgentOrgMembership`, `AgentOrganizationMembership`, `Agent.organization`, `Organization.admin_user`, and every reader of each (`get_user_org`, permissions, the `use_membership_rbac` flag). Choose the canonical model and a dual‑read/backfill plan. Then T‑1031 with permission‑matrix tests. KI‑030 can be fixed alongside.

## Session 5 — 2026-11-21 — M1: membership consolidation + transactional outbox
- **Start state:**
  - The owner had committed session 4 (`b9ac8a7`); the tree was clean.
  - The baseline re‑run gave **1477 passed, 1 skipped**, matching the handoff.
- **Completed:** T‑1030 (ADR‑013 phase A):
  - inventory of all five user↔org links and their readers/writers;
  - `OrganizationMembership` made canonical, with save hooks that sync `Agent.organization`/`Agent.user`/`Organization.admin_user`;
  - `organizations 0024` backfill;
  - `reconcile_memberships` command;
  - deterministic `get_user_org()`.
- **Found and fixed:**
  - An agent removed from a team kept org access through a stale active membership.
  - Agents registered or added to a team after migration 0010, and the owners of admin‑created orgs, resolved to no org.
  - The members‑API delete left `Agent.organization` set.
  - KI‑032: `PIIMaskingMiddleware` masked the phone in JSON agent registration and member invites (400). The two endpoints are now exempt; a wider audit is T‑1033.
- **Completed (after the owner said "continue"):** T‑1021 (ADR‑014):
  - `OutboxMessage` with an encrypted payload, on‑commit dispatch, relay, purge, lease/backoff/expiry and a configurable queue;
  - adopted for OTPs (resolves KI‑029) and notifications.
- **Found and fixed:**
  - **KI‑033 (Critical):** 12 call sites called `WhatsAppClient` methods on the class, so no OTP, notification or alert had been delivered since `8cee1dd`, and failed OTPs were logged in plaintext.
  - KI‑034: a flaky API‑key test.
- **New tasks:** T‑1022 (outbox for the remaining sends), T‑1032 (membership contract), T‑1033 (masking endpoint audit).
- **Tests:** 1528 passed, 1 skipped, 0 failed (start 1477). No migration drift; `manage.py check` clean; Bandit 0 HIGH. `organizations 0024` and `core 0002` migrated forward → back → forward.
- **Not committed.**

### Handoff → session 6
1. `git status`: session‑5 changes are uncommitted. **Do not commit.**
2. `scripts/dev/test.sh` → expect 1528 passed, 1 skipped.
3. Next: **T‑1100** search indexes (GAP‑040).
   - Start with `apps/properties/services.py` `PropertySearchService._from_db`, `PropertyQuerySet.visible_to` and the `PropertyViewSet.get_queryset` filters in `apps/properties/views.py`.
   - Run `EXPLAIN ANALYZE` on the common filters (city, purpose/type, price range, `is_active` + `is_sandbox`, org) and on the full‑text search.
   - Add composite B‑tree, GIN (tsvector / JSON `cities`) and `pg_trgm` indexes. Use `AddIndexConcurrently` with `atomic = False`, and check that `pg_trgm` is available in every environment.
4. Then T‑1090 → T‑1050 → T‑1031 (starts from the ADR‑013 phase B reader list) → T‑1032 → T‑1022.

## Session 6 — 2026-11-21 — M1: search indexes + OpenAPI
- **Start state:** the owner had committed session 5 (`63c3cd6`); the tree was clean. The baseline re‑run gave **1528 passed, 1 skipped**, matching the handoff.
- **Completed:** T‑1100 (ADR‑015):
  - `pg_trgm` and 4 concurrently built indexes: full‑text GIN, trigram GIN on `UPPER(city)`/`UPPER(location)`, partial rank B‑tree;
  - `?search=` rewritten to an index‑backed, sanitised prefix query, with exact matching for full reference numbers;
  - checked with `EXPLAIN ANALYZE` on 200k rows: no sequential scans; selective full‑text 631 → 3 ms.
- **Found and fixed:**
  - none of the old single‑column indexes matched the `UPPER(col) LIKE` SQL Django emits;
  - the old full‑text search used the server's mutable default config and filtered on rank, so it could never use an index;
  - `OpClass` index SQL is invalid unless `django.contrib.postgres` is installed (added);
  - the default parser splits `RT-…-000000001` so tokens could not match it (ref‑number exact path).
- **Completed:** T‑1090a (ADR‑016):
  - drf‑spectacular OpenAPI 3.1 at `/api/v1/schema/` (staff only by default);
  - CI validates the schema and publishes it as an artefact;
  - a ratchet test stops schema debt from growing (154 schema‑less `APIView`s → KI‑037/T‑1091).
- **Split:** T‑1090 into a–d. The direction for the error envelope (T‑1090b) is recorded in ADR‑016: additive and backward‑compatible.
- **New issues:** KI‑035 (common‑term ranking cost), KI‑036 (`?ref_no=` unindexed), KI‑037 (OpenAPI coverage).
- **Tests:** 1567 passed, 1 skipped, 0 failed (start 1528). No migration drift; `check` clean; Bandit 0 HIGH. `properties 0021/0022` migrated forward → back → forward.
- **Not committed.**

- **Continued (owner said "continue"):**
  - **T‑1090b** (ADR‑017): `RequestIdMiddleware` (sanitised `X-Request-ID`, log field, CORS); additive `error_info {status, code, message, fields}` + `request_id` on every `/api/` JSON error; JSON 404/500 for `/api/`. The key is `error_info` because ~50 views already return `error` as a string (KI‑038/T‑1092).
  - **T‑1090c:** shared `apps/core/query_params.py`; 9 bare `int()` casts in 7 views fixed (negative `limit` was a 500); property list enums/ranges validated and documented in OpenAPI; KI‑036 resolved.
- **Final tests:** 1602 passed, 1 skipped, 0 failed. `check`, migration drift and schema validation clean; Bandit 0 HIGH.

### Handoff → session 7
1. `git status`: session‑6 changes are uncommitted. **Do not commit.** `requirements/base.txt` changed, so run `REBUILD=1 scripts/dev/test.sh` once (expect 1602 passed, 1 skipped).
2. Next: **T‑1090d** `Idempotency-Key` (GAP‑032) for the enquiry/lead create, payment and deal/booking POSTs.
   - Inventory the endpoints first: `apps/leads/views.py` create, `apps/payments/views.py`, `apps/escrow/views.py`.
   - Suggested design (write it as ADR‑018): a `core.IdempotencyRecord` keyed by `(key, user/org, method, path)` with a request‑body hash and the stored status + body, plus a TTL purge in beat.
     - same key + same body → replay the stored response;
     - same key + different body → 422 `idempotency_key_reused`;
     - concurrent in‑flight → 409.
   - Make it opt‑in per view (decorator or mixin), not global.
   - Allow the `idempotency-key` CORS header (`CORS_ALLOW_HEADERS` in `config/settings/base.py`) and document it in the OpenAPI parameters.
3. Then T‑1050 (`Market` entity; inspect the existing `apps/markets` first) → T‑1031 → T‑1032 → T‑1022 → T‑1091/T‑1092.
