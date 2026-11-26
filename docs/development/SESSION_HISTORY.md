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

- **Continued again (owner said "continue"):** **T‑1090d** (ADR‑018): opt‑in `@idempotent` with `core.IdempotencyRecord` (encrypted body, lease, TTL, hourly purge) on lead/appointment create, checkout, SEPA and deal‑lock initiate. Only 2xx outcomes are stored. **T‑1090 is complete.**
- **Final tests:** 1617 passed, 1 skipped, 0 failed. `check` and migration drift clean; Bandit 0 HIGH.

### Handoff → session 7
1. `git status`: the owner committed session 6 up to `591a5ea`; the T‑1090d files may still be uncommitted. **Do not commit.** If the image predates `drf-spectacular`, run `REBUILD=1 scripts/dev/test.sh` once (expect 1617 passed, 1 skipped).
2. Next: **T‑1050** `Market` entity; remove hardcoded PK defaults; UTC + per‑entity TZ (TR‑04, GAP‑014/091).
   - Start by reading `apps/markets/` (it already exists as an installed app, and has not been assessed in these sessions) and the PRD market‑pack sections (`docs/product/ai-real-estate-agent/PRD_MASTER.md`, `TECHNICAL_RECOMMENDATIONS.md` TR‑04).
   - Then inventory the hardcoded defaults: `grep -rn "'PKR'\|'PK'\|marla" apps --include=*.py`. Known ones: `Property.currency`/`country`/`area_unit` defaults, `_DEFAULT_CITY_CODE_MAP`, `PropertySearchService._result_to_dict` "PKR" formatting.
   - Plan expand/contract: add a `market` FK with a PK default, then backfill, then add the CI grep check required by the M1 exit criterion.
3. Then T‑1031 (RBAC phase B, ADR‑013) → T‑1032 → T‑1022 → T‑1091/T‑1092 → T‑1070.

## Session 7 — 2026-11-21 — M1: Market entity
- **Start state:** the owner had committed through T‑1090d (`db23c9a`); the tree was clean.
- **Completed:** T‑1050a (ADR‑019):
  - DB‑backed `Market` (seeded from the registry; PK active), with a cached `get_market()` and a registry fallback;
  - `DEFAULT_MARKET` drives all 11 former literal defaults through DB‑free callables;
  - nullable `market` FK on `Property` and `Organization`, backfilled from country and derived on create;
  - `market_backfill_report`; admin;
  - CI test for the M1 "no hardcoded PK defaults" criterion, plus a ratchet on the remaining literals.
- **Split:** T‑1050 into a–c. The UTC switch (c) is separate because it shifts every beat crontab and report bucket.
- **Checked, no issue:** `get_financing_calculator` imports `apps.markets.<cc>.financing`; those modules exist.
- **Tests:** 1641 passed, 1 skipped, 0 failed (session 6 ended 1617). Migration rehearsal forward → back → forward OK; drift/`check` clean; Bandit 0 HIGH.
- **Not committed.**

- **Continued (owner said "continue"):** T‑1031a (ADR‑020):
  - core permissions and `get_user_org` are membership‑only;
  - `X-Organization-ID` selects the active org, verified against active memberships (foreign org → 403 + `cross_tenant` event);
  - members API grant policy: owner never grantable, org_admin owner‑only;
  - KI‑030 fixed;
  - 22‑test permission matrix.
- **Found and fixed:**
  - the invite API let any org admin create owners/org admins;
  - the security engine treated `X-Organization-ID` as an attack before JWT auth, so the header could never be used.
- **Final tests:** 1661 passed, 1 skipped, 0 failed.

- **Continued:** T‑1031b. Every request‑time legacy org read now uses membership helpers. Management actions use `get_admin_org`, so a viewer membership selected by header grants nothing administrative.

### Handoff → session 8
1. `git status`: session‑7 changes are uncommitted. **Do not commit.** `scripts/dev/test.sh` should pass with 0 failed (see TESTING_STATUS for the count).
2. Next: **T‑1032**, the membership contract. Invite, accept, role change, ownership transfer and removal become one service in `apps/organizations/membership.py`, with audit events and the ADR‑020 grant policy enforced there rather than in `OrgMembershipView`. Add an ownership‑transfer endpoint (owner only, target must be an active member).
3. Then T‑1022 → T‑1050b/c → T‑1091/T‑1092 → T‑1070. ADR‑013 phase C (drop `Organization.admin_user`/`Agent.organization` projections) stays after T‑1032.

## Session 8 — 2026-11-22 — M1: membership contract, outbox adoption
- **Start state:** the owner had committed through T‑1031b (`2fd8ee0`, labelled "session 9"); the tree was clean. Docker Desktop had to be started.
- **Completed:** T‑1032a (ADR‑021):
  - invite, role change, removal and ownership transfer are one service in `apps/organizations/membership.py`, which enforces the grant policy and writes an `AuditLog` row with each change;
  - new `PATCH /me/members/{id}/` and ownership‑transfer endpoints (owner via `/me/`, platform admin via `/{id}/`);
  - the previous owner stays as `org_admin`;
  - the target must be an active `org_admin` with a developer account who owns no other org.
- **Split:** T‑1032 into a/b. b = ADR‑013 phase‑C schema contraction, gated on a clean prod reconcile.
- **Found and fixed:**
  - **KI‑040:** the escrow high‑risk audit row used non‑existent `AuditLog` fields and was never written;
  - the members view served two URLs, which caused 4 operationId collisions; OpenAPI budgets 154/32 → 153/28.
- **Completed:** T‑1022a (ADR‑014 addendum): generic `whatsapp.text` outbox kind, adopted for hot‑lead alerts, verification notices, listing alerts, appointment and stale‑lead reminders, and client re‑engagement.
  - **Found and fixed (KI‑042):** the verification notice was re‑sent on every save.
- **New issues:** KI‑040 (fixed), KI‑041 (no invite acceptance; product decision), KI‑042 (fixed).
- **Tests:** 1713 passed, 1 skipped, 0 failed (session 7 ended 1665). `check`/drift/schema clean; Bandit 0 HIGH.
- **Not committed.**

- **Continued (owner said "continue"):** T‑1022b. The escrow expiry notices, the seller deal‑lock code, the audit companion text and the scraped follow‑up now go through the outbox. Campaigns and AI re‑engagement stay direct because they are rate‑limited. **T‑1022 complete.**
  - **Found and fixed (KI‑043):** the seller's confirmation code was sent before AML screening, so it went out even for blocked buyers.
- **Continued:** T‑1050b‑1. Market‑neutral helpers; all 20 `or 'PK'` fallbacks removed; prices are formatted in the listing's currency.
  - **Found and fixed (KI‑044):** non‑PK listings were shown as "PKR …".
  - Ratchet PKR 34 → 27, PK fallback 20 → 0.
- **Final tests:** 1723 passed, 1 skipped, 0 failed. `check`/drift/schema clean; Bandit 0 HIGH.

- **Continued (owner committed the earlier work, then said "continue"):**
  - **T‑1050b‑2:** code that is PK by design now uses `apps.markets.pk` constants.
    - Audit, PDF and WhatsApp audit money is labelled in its real currency; benchmark figures use the benchmark currency.
    - Fixed KI‑046: the PDF "Today" projection row was always 0.
    - Recorded KI‑045: the audit engine is PK‑only.
    - Ratchet PKR 27 → 5. **T‑1050b complete.**
  - **T‑1050c (ADR‑022):** UTC `TIME_ZONE`/`CELERY_TIMEZONE`; crontabs keep their former instants.
    - Monthly reports are now per‑org local months, hourly on UTC day 1.
    - Report and stat buckets and "today" counts use the org's market zone.
    - Fixed KI‑047: messages showed UTC times.
    - **T‑1050 complete.**
- **Final tests:** 1738 passed, 1 skipped, 0 failed. `check`/drift/schema clean; Bandit 0 HIGH.

- **Continued again (owner committed session 12, said "continue"):**
  - **T‑1070 design:** ADR‑023 splits T‑1070 into subtasks a–g. Evolve `Property` in place; rolling‑safe expand migrations; the contract step (T‑1070g) needs owner approval.
  - **T‑1070a:** listings now have purpose, bedrooms, bathrooms, floor, total floors, year built, parking, pets and price terms (type, negotiable, on request, deposit, service charge), plus a size‑capped `attributes` object.
    - **Validation:** shared cross‑field rules in `Property.v2_errors`.
    - **API:** the new fields, plus list filters for purpose and minimum bedrooms/bathrooms.
    - **Search and AI:** search and the AI search tool honour purpose and bedrooms. Rent searches skip the sale‑only scrapers (KI‑048).
    - **Migrations:** 0025 adds the columns with DB defaults; 0026 builds the index concurrently.
  - **T‑1070b:** `size_value`/`size_unit`/`size_m2`/`plot_size_m2` added, backfilled, and kept in sync with the deprecated `area_*` fields by `save()`. A `sync_listing_size` command repairs rows written by old pods during a rollout.
    - **Readers:** moved to the canonical size.
    - **Fixed KI‑049:** kanal and sqft listings were scored and matched as if their number were marla. The copilot also now sends bedrooms.
  - **T‑1070c:** `PriceHistory` is recorded atomically by `Property.save()` with an explicit actor/source context (API, admin, AI tool), backfilled with one baseline row per listing, and exposed read‑only at `properties/{id}/price-history/`.
    - **Fixed:** the load snapshot no longer marks unsaved in‑memory values as persisted after a partial `save(update_fields=…)`.
- **Final tests:** 1781 passed, 1 skipped, 0 failed; `check`/drift/schema clean; Bandit 0 HIGH.

### Handoff → session 9
1. `git status`: T‑1070c is uncommitted (owner committed T‑1070a/b). **Do not commit.** `scripts/dev/test.sh` should give 1781 passed, 1 skipped.
2. Next: **T‑1070d** taxonomy and amenities (see the DEVELOPMENT_STATUS "Next task"). Then e (market attribute schema), f (tsvector). Hold g (contract) for owner approval.
3. After that: T‑1080 (lifecycle) → T‑1011 (MFA) → T‑1091/T‑1092 (OpenAPI/error cleanup, P3).

## Session 9 — 2026-11-23 — M1: listing taxonomy, amenities, market attribute schema
- **Start state:** the owner had committed session 8 (`c113704`) and the tree was clean. T‑1070c was confirmed in the code (migrations 0029/0030, `PriceHistory`, tests). Docker Desktop had to be started.
- **Completed: T‑1070d** (ADR‑023 addendum).
  - `category` (adds land and hospitality) and `subtype` are now canonical. `property_type` is a deprecated alias that `save()` keeps in sync, and `sync_listing_taxonomy` repairs rollout drift.
  - New `Amenity` taxonomy: 35 seeded entries with `en`/`ur` labels and per‑market availability, attached to listings through `ListingAmenity`.
  - API: amenities read and written by key; new list filters (`amenities` means all must match); new `GET properties/taxonomy/`.
  - Search service and AI search tool: amenities are a hard filter, and amenity searches skip the scrapers.
- **Completed: T‑1070e** (ADR‑023 addendum). `Market.listing_schema` is a small typed format with seeds for PK, AE and GB. It is validated in `Market.clean`; listing attributes are checked in `Property.clean` and in the API.
- **New issues:** KI‑050 (`market` is not re‑derived when `country` changes; decision needed) and KI‑051 (float `size_m2` on an in‑memory instance after save).
- **Tests:** 1813 passed, 1 skipped, 0 failed (session 8 ended at 1781). `check`, drift and schema checks are clean; migrations rehearsed forward → back → forward; Bandit 0 HIGH.
- **Not committed.**

- **Continued (owner said "ask me for blockers and continue"):** asked 4 questions. The owner chose **Google Maps** geocoding with PostGIS (T‑1060), a **per‑endpoint PII allow‑list** (T‑1033), **pending invites with accept/decline** (KI‑041), and **market re‑derived on a country change** (KI‑050).
  - **KI‑050:** `save()` re‑derives `market` when `country` changes.
  - **T‑1033:** `PRIVACY_MASK_FIELD_ALLOWLIST` replaces two path exemptions; free text stays masked.
  - **T‑1070f:** trigger‑maintained `search_tsv` plus a GIN index; search uses it, with results equal to the legacy query. Broad terms are 6–15× faster on 200k rows. KI‑035 resolved.
  - **T‑1032c:** `OrganizationInvite` with accept/decline/revoke/expiry. The grant is re‑checked with the inviter's current rights at acceptance; the invitee is notified through the outbox; every step is audited. KI‑041 resolved.
  - **Caught in testing:** two state changes (expire, void) were being rolled back because a 409 was raised inside the atomic block. The conflict is now raised after commit.
  - **Process slip:** one full run was invalid because source was edited during it. It was re‑run clean.
- **Final tests:** 1839 passed, 1 skipped, 0 failed; check/drift clean; migrations rehearsed; Bandit 0 HIGH.
- **Not committed.**

### Handoff → session 10
1. `git status`: the owner committed through T‑1011 (`1a1ed64`). Uncommitted: **T‑1110**:
   - `apps/core/images.py`, the `sanitize_images` command;
   - `properties 0048`, `agents 0014`;
   - image API, serializer and model changes;
   - staged contract migrations renumbered 0049/0050;
   - `tests/test_image_uploads.py`.
   **Do not commit.**
2. **Deploy:** GO_LIVE §27. Run the `sanitize_images` backfill and check the proxy body limit.
3. Next: **T‑1120** AI provider abstraction and prompt registry, then T‑1032b. Open: KI‑055…KI‑058.

- **Continued ("continue"): T‑1120.** LLM router (`apps/ai/llm.py`), prompt registry (`apps/ai/prompts.py`) and the OpenAI‑compatible provider. Call sites are migrated and `GeminiClient` is removed. Failover is off by default (order = `AI_BACKEND`). ADR‑028, GO_LIVE §28, KI‑059. Tests: 1934 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 10 (supersedes the one above)
1. `git status`: the owner committed through T‑1110 (`a215499`). Uncommitted: **T‑1120**:
   - `apps/ai/{llm,prompts}.py`, `backends/openai_compat.py`;
   - backend changes, `ai 0003`, `client.py` deleted;
   - scoring/agent/knowledge/leads/re‑engagement migrated;
   - settings;
   - `tests/test_llm.py`.
   **Do not commit.**
2. **Owner decision before enabling failover:** a paid OpenAI‑compatible provider (GO_LIVE §28).
3. Next: **T‑1032b**. Open: KI‑055…KI‑059.

- **Continued ("continue"): T‑1032b.** Gated on production data and destructive, so I asked. The owner chose **staged** and **keep `Agent.organization`**. Done:
  - b‑1: the owner membership is canonical, multi‑org owners are allowed, and collaboration terms are linked;
  - b‑2 drops are staged with a gate and rehearsed.

  ADR‑013 addendum, GO_LIVE §29. Tests: 1946 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 10 (supersedes the ones above)
1. `git status`: the owner committed through T‑1120 (`2bd9b9b`). Uncommitted: **T‑1032b‑1**:
   - `membership.py`, models, migrations `organizations 0028`/`agents 0015`;
   - `contract_migrations/` in organizations and agents;
   - readers in organizations/agents/compliance/ai/inventory/billing/marketplace/leads;
   - tests.

   **Do not commit.**
2. **After deploy:** `reconcile_memberships --apply`, then resolve the report‑only categories (GO_LIVE §29).
3. Next: **T‑1040** (`is_sandbox` for leads with no organization, KI‑017). T‑1070g‑2 and T‑1032b‑2 wait for their releases. Open: KI‑055…KI‑059.

- **Continued ("continue"): T‑1040.** Lead and session `is_sandbox` now follows the organization (insert and org change); no organization means live. Batched backfills. KI‑017 resolved; ADR‑002 addendum; GO_LIVE §30. Tests: 1954 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 10 (supersedes the ones above)
1. `git status`: the owner committed through T‑1032b‑1 (`4ccd24f`). Uncommitted: **T‑1040**:
   - `apps/organizations/sandbox.py`;
   - `Lead`/`WhatsAppSession`;
   - `leads 0021`, `whatsapp 0013`;
   - tests.

   **Do not commit.**
2. **M1:** all code tasks are done. T‑1070g‑2 and T‑1032b‑2 are staged and wait for a deployed release plus a clean production check (`check_listing_contract`, `reconcile_memberships`).
3. **Next:** start **M2**. Create `milestones/MILESTONE_02_*.md` from PRD Phase 2 (as was done for M1), then **T‑2010**, the explicit Gemini tool loop. It also routes the KI‑059 call sites. Open: KI‑055…KI‑059.

- **Continued ("continue"): M2 started.** Created the milestone file (15 tasks from PRD Phase 2 and the AI spec). **T‑2010:**
  - the explicit tool‑loop runtime, the tool registry with policies, and provider `step()` adapters;
  - per‑step usage and tool‑call auditing;
  - verbatim terminal tools for every provider;
  - a refined failover rule;
  - the SDK chat loops removed.

  ADR‑029, GO_LIVE §31. Tests: 1966 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 10 (supersedes the ones above)
1. `git status`: the owner committed through T‑1040 (`46c2ae4`). Uncommitted:
   - `milestones/MILESTONE_02_AI_AGENT_MVP.md`;
   - **T‑2010**: `apps/ai/{runtime,tool_registry}.py`, the backends' `step()`, `llm.chat`, the agent, settings, `tests/test_runtime.py`, `tests/test_llm.py`.

   **Do not commit.**
2. Next: **T‑2012** durable `PendingAction` confirmations, enforced by the runtime for `requires_confirmation` tools; then remove the `_is_agent_request` bypass. Then T‑2011 (AITrace + cost).
3. **Open:** KI‑055…KI‑059. KI‑059 (OCR, STT and intent classifier not on the router) is not closed by T‑2010.

## Session 10 — 2026-11-24 — T‑2012, T‑2011
- **Completed:** T‑2012 (ADR‑030) and T‑2011 (ADR‑031). Found KI‑060…KI‑063 (061 and 063 fixed).
- **Tests:** 2014 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 11 (supersedes the ones above)
1. `git status`: uncommitted work is T‑2012 and T‑2011 (see IMPLEMENTATION_LOG). **Do not commit.**
2. Next: **T‑2013** grounding checker v2. Start in `apps/ai/compliance_filters.py`, `apps/ai/service.py` `_process` (step 5b), and `apps/ai/runtime.py` (collect tool results as sources). Then T‑2020.
3. Quick fix worth doing first: KI‑060 (AML session block). Owner/operator: enter `ModelPrice` rows (GO_LIVE §33).

- **Continued ("continue"): T‑2013** (ADR‑032) and KI‑060. Found KI‑064. Tests: 2035 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 11 (supersedes the one above)
1. `git status`: uncommitted work is T‑2013 and KI‑060. **Do not commit.**
2. Next: **T‑2020** (see DEVELOPMENT_STATUS for the starting files). KI‑064 is a small fix worth doing first.

## Session 11 — 2026-11-24
- **Completed:** KI‑064, T‑2020 (ADR‑033), KI‑062 and the KI‑063 residual. Found KI‑065 and KI‑066.
- **Tests:** 2066 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 12 (supersedes the one above)
1. `git status`: uncommitted work is T‑2020, KI‑062, KI‑064. **Do not commit.**
2. Next: **T‑2030** (starting files in DEVELOPMENT_STATUS).
3. Owner: KI‑065 (web workers) and KI‑066 (chat text in exports) need decisions before web chat goes live.

## Session 12 — 2026-11-24
- **Completed:** T‑2030 (ADR‑034) and KI‑067. Found KI‑068, KI‑069 and KI‑070.
- **Tests:** 2108 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 13 (supersedes the one above)
1. `git status`: uncommitted work is T‑2030 and KI‑067. **Do not commit.**
2. Next: **T‑2031** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions still open: KI‑065, KI‑066.

## Session 13 — 2026-11-24
- **Completed:** T‑2031 (ADR‑035) and KI‑070. Found KI‑071.
- **Tests:** 2130 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 14 (supersedes the one above)
1. `git status`: uncommitted work is T‑2031 and KI‑070. **Do not commit.**
2. Next: **T‑2050** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions still open: KI‑065, KI‑066.

## Session 14 — 2026-11-24
- **Completed:** T‑2050 (ADR‑036). Split out T‑2051. Found KI‑072…KI‑075.
- **Tests:** 2170 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 15 (supersedes the one above)
1. `git status`: uncommitted work is T‑2050. **Do not commit.**
2. Next: **T‑2060** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions still open: KI‑065, KI‑066, KI‑074; email provider.

## Session 15 — 2026-11-24
- **Completed:** T‑2060 (ADR‑037). Found KI‑076, KI‑077.
- **Tests:** 2207 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 16 (supersedes the one above)
1. `git status`: uncommitted work is T‑2060. **Do not commit.**
2. Next: **T‑2040** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions still open: KI‑065, KI‑066, KI‑074; email provider.

## Session 16 — 2026-11-24
- **Completed:** T‑2040 (ADR‑038). Found KI‑078…KI‑080.
- **Tests:** 2242 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 17 (supersedes the one above)
1. `git status`: uncommitted work is T‑2040. **Do not commit.**
2. Next: **T‑2041** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions still open: KI‑065, KI‑066, KI‑074; email provider; WhatsApp template.

## Session 17 — 2026-11-24
- **Completed:** T‑2041 (ADR‑039). Found KI‑081, KI‑082; fixed a grounding number‑parsing bug.
- **Tests:** 2270 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 18 (supersedes the one above)
1. `git status`: uncommitted work is T‑2041. **Do not commit.**
2. Next: **T‑2014** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions still open: KI‑065, KI‑066, KI‑074, KI‑081; email provider; WhatsApp template.

## Session 18 — 2026-11-24
- **Completed:** T‑2014 (ADR‑040); owner decisions KI‑065, KI‑066, KI‑074 (ADR‑041). Found KI‑083…KI‑086.
- **Tests:** 2318 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 19 (supersedes the one above)
1. `git status`: uncommitted work is T‑2014 and the owner decisions. **Do not commit.**
2. Next: **T‑2016** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions still open: KI‑085; email provider and WhatsApp template (go‑live).

## Session 19 — 2026-11-24
- **Completed:** T‑2016 (ADR‑042). Fixed city matching and KI‑089 (tokens). Found KI‑087, KI‑088. **Tests:** 2324 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 20 (supersedes the one above)
1. `git status`: uncommitted work is T‑2016. **Do not commit.**
2. Next: **T‑2070** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions open: KI‑085, KI‑089 (relax `;` and `--`); email provider and WhatsApp template (go‑live).

## Session 20 — 2026-11-25
- **Verified:** tree clean at `a33c7fa`; baseline re-run gave 2324 passed, 1 skipped, as handed over.
- **Completed:** T‑2070 (ADR‑043). Split out T‑2071, T‑2072.
- **Found by the new evals and fixed:** the input guard had no Urdu or Roman Urdu patterns and missed common English system-prompt requests; it blocked "without any restrictions on pets" as an attack and refused Roman Urdu or rental messages naming a hospital; the fairness policy missed most Urdu and Roman Urdu wording and treated "save this search" in Urdu as a request for a safe area; grounding missed Urdu bedroom, bathroom and verification claims.
- **Found:** KI‑090…KI‑092. **Tests:** 2355 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 21 (supersedes the one above)
1. `git status`: uncommitted work is T‑2070. **Do not commit.**
2. Next: **T‑2071** (starting files in DEVELOPMENT_STATUS), then T‑2051.
3. Owner decisions open: T‑2072 (reviewers, API key and budget for live evals), KI‑085, KI‑089; email provider and WhatsApp template (go-live).

## Session 21 — 2026-11-25
- **Verified:** the owner committed T‑2070 (`ab7af9a`); the tree was clean.
- **Completed:** T‑2071 (ADR‑044). **Found and fixed:** assistant replies could pass on links, phone or account numbers from a listing's text; listing titles and organization names entered the system prompt raw; geo search ignored PostGIS for up to 5 minutes after a host boot (cache age measured from 0).
- **Found:** KI‑093. **Tests:** 2364 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 22 (supersedes the one above)
1. `git status`: uncommitted work is T‑2071. **Do not commit.**
2. Next: **T‑2051** (starting files in DEVELOPMENT_STATUS).
3. Owner decisions open: T‑2072, KI‑085, KI‑089; email provider and WhatsApp template (go-live).

## Session 22 — 2026-11-25
- **Verified:** the owner committed T‑2071 (`0042a45`); the tree was clean.
- **Completed:** T‑2051 (ADR‑045). **Found and fixed:** the privacy masking corrupted ids with long digit runs, so about 1 in 850 listings could not be enquired about (seen as one unexplained 400 in an existing test); idempotency compared uploads by file name only; a viewing could be moved after a block.
- **Found:** KI‑094, KI‑095. **Tests:** 2386 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 23 (supersedes the one above)
1. `git status`: uncommitted work is T‑2051. **Do not commit.**
2. Next: staging end-to-end script for MVP criterion 1, then KI‑094.
3. Owner decisions open: T‑2072, KI‑085, KI‑089; email provider and WhatsApp template (go-live).

## Session 23 — 2026-11-25
- **Verified:** the owner committed T‑2051 (`a64cf24`); the tree was clean.
- **Completed:** the end-to-end journey for MVP criterion 1 (ADR‑046): web, assistant and WhatsApp, 69 checks, repeatable.
- **Found and fixed:** KI‑096 (single-worker deployments did not read the `high-resource` queue).
- **Tests:** 2388 passed, 1 skipped, 0 failed. The journey fails as it should with the AI worker stopped and with a wrong webhook signature. **Not done:** a staging run; a real model. **Not committed.**

### Handoff → session 24 (supersedes the one above)
1. `git status`: uncommitted work is the E2E journey. **Do not commit.**
2. Next: KI‑094 (starting files in DEVELOPMENT_STATUS).
3. Owner: staging E2E run, T‑2072, KI‑085, KI‑089, KI‑095 (retention), start command check (KI‑096); email provider and WhatsApp template.

## Session 24 — 2026-11-25
- **Verified:** the owner committed session 23 (`d78b396`); the tree was clean.
- **Completed:** KI‑094, and KI‑097 found on the way (ADR‑047). **Found:** KI‑098.
- **Tests:** 2402 passed, 1 skipped, 0 failed; E2E journey 69 checks. **Not committed.**

### Handoff → session 25 (supersedes the one above)
1. `git status`: uncommitted work is KI‑094/KI‑097. **Do not commit.**
2. Next: MVP criteria 3 and 5 (starting files in DEVELOPMENT_STATUS).
3. Owner: KI‑098, staging E2E run, T‑2072, KI‑085, KI‑089, KI‑095, KI‑096 start command; email provider and WhatsApp template.

## Session 25 — 2026-11-25
- **Verified:** the owner committed session 24 (`90c13ed`); the tree was clean.
- **Completed:** MVP criteria 3 and 5 (ADR‑048). **Found and fixed:** KI‑099, KI‑100. **Recorded:** KI‑101.
- **Tests:** 2417 passed, 1 skipped, 0 failed; E2E journey 69 checks. **Not committed.**

### Handoff → session 26 (supersedes the one above)
1. `git status`: uncommitted work is session 25. **Do not commit.**
2. Next: MVP criteria 10 and 11 (starting files in DEVELOPMENT_STATUS).
3. Owner: KI‑098, staging E2E run, T‑2072, KI‑085, KI‑089, KI‑095, KI‑096; email provider and WhatsApp template; frontend check of checklist §49.

## Session 26 — 2026-11-25
- **Verified:** the owner committed session 25 (`65b93d2`); the tree was clean.
- **Completed:** MVP criteria 10 and 11 (ADR‑049). **Found and fixed:** KI‑102, KI‑103. **Recorded:** KI‑104, KI‑105.
- **Tests:** 2434 passed, 1 skipped, 0 failed. E2E journey 69 checks. **Not committed.**

### Handoff → session 27 (supersedes the one above)
1. `git status`: uncommitted work is session 26. **Do not commit.**
2. Next: MVP criteria 4 and 14 (starting files in DEVELOPMENT_STATUS).
3. Owner: KI‑085, KI‑105, KI‑098, staging E2E run, T‑2072, KI‑089, KI‑095, KI‑096; email provider and WhatsApp template.

## Session 27 — 2026-11-25
- **Verified:** the owner committed session 26 (`fa2f741`); the tree was clean.
- **Completed:** MVP criteria 4 and 14 (ADR‑050). **Found and fixed:** KI‑106 (worker and multi-worker metrics never exported), KI‑107. **Recorded:** KI‑108, KI‑109, KI‑110.
- **Tests:** 2464 passed, 1 skipped, 0 failed; promtool rule tests pass. **Not committed.**

### Handoff → session 28 (supersedes the one above)
1. `git status`: uncommitted work is session 27. **Do not commit.**
2. Next: MVP criterion 15, runbooks (starting files in DEVELOPMENT_STATUS).
3. Owner: KI‑108 alert receiver, KI‑109 production Prometheus/Grafana, KI‑085, KI‑105, KI‑098, staging E2E run, T‑2072, KI‑089, KI‑095, KI‑096; email provider and WhatsApp template.

## Session 28 — 2026-11-25
- **Verified:** the owner committed session 27 (`7d95593`).
- **Asked the owner** the open blockers; answers in DEVELOPMENT_STATUS (2026‑11‑25).
- **Completed:** ADR‑051: self-hosted monitoring stack with email alerts (checked end to end), bearer token for `/metrics/`, export for everyone, Cloudinary files deleted on erasure. **Found and fixed:** KI‑111. **Recorded:** KI‑112.
- **Tests:** 2477 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 29 (supersedes the one above)
1. `git status`: uncommitted work is session 28. **Do not commit.**
2. Next: MVP criterion 15, runbooks (starting files in DEVELOPMENT_STATUS).
3. Owner: deploy the monitoring stack (§52), KI‑112, KI‑098, staging E2E run, T‑2072, KI‑089, KI‑095, KI‑096; email provider and WhatsApp template.

## Session 29 — 2026-11-25
- **Completed:** MVP criterion 15 (ADR‑052): runbooks for LLM outage, WhatsApp token expiry, data incidents, harmful AI output, plus short notes for the other alerts; every alert links to one (checked by tests); five new alerts and the metrics behind them; token check covers organization tokens and emails admins; `rotate_field_keys` and `revoke_sessions`.
- **Found and fixed:** KI‑113 (outbox WhatsApp sends counted as sent when only buffered), KI‑115. **Recorded:** KI‑114.
- **Tests:** 2491 passed, 1 skipped, 0 failed. **Not committed.**

### Handoff → session 30 (supersedes the one above)
1. `git status`: uncommitted work is sessions 28 and 29. **Do not commit.**
2. Next: MVP criterion 13 (CI, staging auto-deploy, production approval).
3. Owner: deploy the monitoring stack (§52), runbook roles (§53), KI‑112, KI‑114 rule, KI‑098, staging E2E run, T‑2072, pentest (criterion 12).

## Session 30 — 2026-11-25/26
- **Asked the owner:** deploy target (Docker over SSH) and approval (GitHub environment reviewers).
- **Completed:** MVP criterion 13 (ADR‑053). Dependency vulnerabilities fixed and gated in CI.
- **Found and fixed:** KI‑116…KI‑120. The production compose stack could not have started before this session (KI‑119, KI‑120).
- **Tests:** 2506 passed, 1 skipped, 0 failed; deploy rehearsed on the real images. **Not committed.**

### Handoff → session 31 (supersedes the one above)
1. `git status`: uncommitted work is sessions 28–30. **Do not commit.**
2. Owner: servers and GitHub environments (§54), then watch the first `main` run; monitoring (§52); runbook roles (§53); staging E2E; T‑2072; pentest; KI‑112; KI‑114 rule.
3. Dev: open KIs, or plan Milestone 3.

## Session 31 — 2026-11-26
- **Verified:** the owner committed sessions 28–30 (`014affb`); the tree was clean. No MVP criterion is waiting on development alone, so ready backend KIs were taken.
- **Completed:** KI‑076 (ADR‑054): `my_viewings`, `cancel_viewing`, `reschedule_viewing`, changes only after YES, scoped to the conversation's organization; E2E covers move and cancel. KI‑083 (ADR‑055): live-chat streams hide withdrawn numbers. KI‑087: embedding cache, `sentence-transformers` and `numpy` removed. KI‑088 mitigated. KI‑121 tax sheet fixed.
- **Found:** KI‑092's web path does not run (`run_verification_task` has no caller); KI‑121 (copilot tax sheet and document links queried fields that do not exist).
- **Tests:** 2507 passed, 1 skipped, 0 failed on the rebuilt image; E2E 94 checks; `pip check`, `pip-audit` clean. **Not committed.**

### Handoff → session 32 (supersedes the one above)
1. `git status`: the owner committed KI‑076/KI‑088 as `b955b80`; uncommitted is the rest of session 31 (KI‑083, KI‑121, KI‑087), including the deleted `tests/test_semantic_cache.py` (unstaged). **Do not commit.** Rebuild the test image (`REBUILD=1`) if it predates this session.
2. Dev: M3 planning (create `milestones/MILESTONE_03_PROFESSIONAL_WORKSPACE.md` from PRODUCT_ROADMAP Phase 3), then its first task that needs no owner input.
3. Owner: servers and environments (§54), monitoring (§52), runbook roles (§53), staging E2E, T‑2072, pentest, KI‑112, KI‑114 rule, KI‑121 document links.

## Session 32 — 2026-11-26
- **Verified:** the owner committed session 31 (`b955b80`, `2179546`); the tree was clean.
- **Started M3:** milestone file from PRODUCT_ROADMAP Phase 3 with 11 tasks, additive ones first.
- **Completed:** T‑3010 (ADR‑056) agent performance from enquiry replies, viewings, feedback and leads; T‑3020 (ADR‑057) tasks and follow-ups with reminders.
- **Tests:** 2523 passed, 1 skipped, 0 failed; no migration drift. **Not committed.**

### Handoff → session 33 (supersedes the one above)
1. `git status`: uncommitted work is session 32. **Do not commit.**
2. Dev: T‑3060 listing analytics (start in `apps/properties/public_listings.py`, `listing_search.py`, a daily counter model), then T‑3050.
3. Owner: as before (§54, §52, §53, staging E2E, T‑2072, pentest, KI‑112, KI‑114, KI‑121 document links); new: checklist §55, pricing for T‑3080, the weekly-use metric (M3 exit 3).

## Session 33 — 2026-11-26
- **Verified:** the owner committed session 32 (`c9ae6c0`); the tree was clean; T‑3010/T‑3020 code and tests present.
- **Completed:** T‑3060 (ADR‑058) listing analytics: daily counters per listing for impressions, detail views (once per visitor per day, keyed hash in the cache only), saves and enquiries, written at the event sites; `GET /api/v1/reports/listing-performance/` with rates, totals and a daily series.
- **Fixed on the way:** the staged T‑1070g contract migrations renumbered to 0051/0052 on top of the new `properties 0050` (the guard test caught it).
- **Recorded:** KI‑122 (CDN would hide cached reads).
- **Completed (continued on request):** T‑3050 (ADR‑059) AI listing assistant: missing-info from publish rules, category and market schema; title/description drafts whose sentences are re-checked against the fields and notes (unsupported ones left out); field suggestions from text that must quote it; nothing saved. Eval suite `listing_copy` added to the CI gate.
- **Completed (continued on request):** T‑3070 (ADR‑060) one audit trail: append-only `AuditEvent` with a hash chain that stays linear under concurrent writers, a trigger that refuses changes except erasure's redaction, the three older logs mirrored into it, read API, verify command and daily check. **Recorded:** KI‑123 (contract phase).
- **Completed (continued on request):** T‑3090 (ADR‑061) AI ops console APIs: overview, prompts and versions in use, providers with breaker and price, adding model prices, tools with call outcomes, offline eval runs stored as history, traces without people.
- **Completed (continued on request):** T‑3030 (ADR‑062) pipeline stages per organization beside the AI's lead status: defaults on first use, add/rename/reorder/archive, board, move-stage; won/lost close routing.
- **Tests:** 2593 passed, 1 skipped, 0 failed at session end (2542 after T‑3060, 2557 after T‑3050, 2571 after T‑3070, 2582 after T‑3090); no migration drift; OpenAPI valid; eval gate passed. **Not committed.**

### Handoff → session 34 (supersedes the one above)
1. `git status`: the owner committed T‑3060 (`7a327cc`), T‑3050 (`0106087`), T‑3070 (`fb063cf`) and T‑3090 (`0d8eb0a`); uncommitted is T‑3030. **Do not commit.**
2. Dev: T‑3040 team assignment rules (start files in DEVELOPMENT_STATUS), then T‑3100.
3. Owner: as before (§54, §52, §53, staging E2E, T‑2072, pentest, KI‑112, KI‑114, KI‑121 document links, pricing for T‑3080, weekly-use metric); new: checklist §56, §57 (assistant model calls cost money; rate and flag) §58 (fixed `AUDIT_CHAIN_KEY`), §59 (enter model prices) and §60 (pipeline stages).

## Session 34 — 2026-11-26
- **Verified:** the owner committed T‑3030 (`89982b9`); the tree was clean; pipeline code and tests present.
- **Completed:** T‑3040 (ADR‑063) team management and assignment rules: per-organization strategy (best match, round robin, least loaded), city/area/language/specialization matches (off, prefer, require), caps per organization and per agent, pausing an agent, availability; team roster API; auto-assign job and endpoint on the rules.
- **Fixed on the way:** `bulk-assign` let a developer assign leads to another organization's agent and bypassed activity rows and `routing_state`; no assign path set `routing_state = agent_assigned`; `assign` refused the organization's freelance collaborators.
- **Completed (continued on request):** T‑3100a (ADR‑064, migration plan A/B/C) unified contact and messages, expand phase: SQL view over both message logs (mirrors shown once, KI‑073 mirrors left out), contacts API (person ↔ their leads, organization notes and tags), lead and contact message lists with cursor paging; summaries and suggested replies now see the whole chat. **Recorded:** KI‑124 (writers still split until T‑3100b); KI‑073 partly resolved.
- **Completed (continued on request):** T‑3100b (ADR‑064 addendum) one message log: `conversation_messages` is the one writer table (session, organization, type, media, optional lead); every WhatsApp writer goes through `apps/whatsapp/history.record`; the mirror is gone; every reader sees old and new rows through the view by session; batched, idempotent backfill command. **Fixed:** KI‑124; the live-chat consumer used one fixed fallback id per session; the re-engagement prompt read other organizations' chats with the same phone.
- **Started M4** (continued on request; M3 left only owner-blocked and release-gated work): T‑4010 (ADR‑065) investment calculator with sourced assumptions and checked, labelled commentary (flag off).
- **Tests:** 2633 passed, 1 skipped, 0 failed at session end (2607 after T‑3040, 2616 after T‑3100a, 2624 after T‑3100b); no migration drift; `leads 0028` rehearsed forward/reverse. **Not committed.**

### Handoff → session 35 (supersedes the one above)
1. `git status`: the owner committed T‑3040 (`2a83b59`) and T‑3100a (`08560a1`); uncommitted are T‑3100b and T‑4010. **Do not commit.**
2. Dev: next is T‑4020 (price context), then T‑4030, T‑4060 ([MILESTONE_04](milestones/MILESTONE_04_AI_INTELLIGENCE.md)). T‑3100c waits for the production backfill (§63) and an owner decision on KI‑073 mirrors.
3. Owner: as before (§54, §52, §53, staging E2E, T‑2072, pentest, KI‑112, KI‑114, KI‑121 document links, pricing for T‑3080, weekly-use metric); new: checklist §61, §62, §63, §64 (legal sign-off, then `feature_investment_commentary`); whether web assistant chats should be visible to organizations.
