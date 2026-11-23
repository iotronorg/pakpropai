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
1. `git status`: the owner committed everything through T‑1060a as `6fc5634`. Uncommitted: **T‑1070g‑1 only** (`properties 0040/0041`, `listing_contract.py`, `check_listing_contract`, `apps/properties/contract_migrations/`, the reader/serializer/test changes). **Do not commit.** `scripts/dev/test.sh` should give 1862 passed, 1 skipped.
2. **Deploy order** for this batch: run `migrate` before the new pods serve (GO_LIVE §22). After the rollout, run `check_listing_contract`. **T‑1070g‑2** is staged; move it in only for the following release (see the README there).
3. Next: **T‑1060b** (PostGIS test image + point/GiST/radius; deploy only once PostGIS exists), then T‑1060c (Google, flag off, mocked), then T‑1080 → T‑1011 → T‑1110 → T‑1120 → T‑1032b.
