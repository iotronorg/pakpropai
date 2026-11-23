# Development Tasks

Status: `[ ]` not started · `[~]` in progress · `[x]` completed (tests executed & passed) · `[!]` blocked · `[?]` needs review / decision.
Priority: P0 (blocker) → P3.

## M0 — Stabilisation (Phase 0)

| ID | Status | P | Task | Notes |
|---|---|---|---|---|
| T‑0001 | [x] | P0 | Product assessment and PRD set | `docs/product/ai-real-estate-agent/` (session 1) |
| T‑0002 | [x] | P0 | Reproducible test environment + baseline | `scripts/dev/test.sh`; baseline 1361/1/1; root `conftest.py` cache isolation; export test fixed |
| T‑0003 | [x] | P0 | CI workflow fix | All jobs: repo‑root paths, `development` branch, Bandit HIGH cleared, out‑of‑repo frontend build removed, `infra/scripts/health_check.py` added (tested locally). Validated as YAML + locally; **not yet run on GitHub Actions** |
| T‑0004 | [x] | P0 | Sandbox integrity + consumer visibility (ADR‑002) | Model derivation, 3 backfill migrations, `visible_to()`, search/tool/recs/client API |
| T‑0005 | [x] | P0 | Semantic cache leakage (ADR‑003) | Opt‑in flag, `general_query` only, no digits, org+lang scope |
| T‑0006 | [x] | P0 | Real token accounting (ADR‑004) | `apps/ai/usage.py`; Gemini/Ollama/GeminiClient; AFC residual (KI‑006) |
| T‑0007 | [x] | P0 | Side‑effect tool confirmation (`list_property`, `connect_to_agent`) + deal‑lock freeze (ADR‑005/006) | Audit tool split out to T‑0017. Also fixed `list_property` constraint bug (KI‑021) |
| T‑0008 | [x] | P0 | Scrapers off by default | Messaging updated; code kept |
| T‑0009 | [x] | P1 | Wire `ComplianceEngine` into AI output path | Checked after output guard; single in‑focus DB listing registered by the search tool; 4 tests |
| T‑0010 | [x] | P0 | Field‑level encryption of secrets/PII (ADR‑008) | Approved 2026‑11‑21. 7 migrations, blind index for verify token, encrypted config cache, fail‑closed prod check; forward/reverse rehearsed on Postgres |
| T‑0011 | [x] | P1 | Committed‑artifact removal | Approved. 7 files removed from working tree + `.gitignore` rules. Still in git history (no commits/rewrite per owner) |
| T‑0012 | [x] | P2 | Compare/market‑trends shadowing + param validation | |
| T‑0013 | [x] | P1 | Audit "not a valuation" disclaimer + provenance | Page‑1 notice + corrected final disclaimer in PDF; WhatsApp summary relabelled; `provenance` block in audit data |
| T‑0014 | [x] | P3 | README drift | Cloudinary, no `render.yaml`, encryption keys, test command. Branding (PakProp vs RealTron) unchanged — product decision |
| T‑0015 | [x] | P1 | Freeze flags for frozen modules | Deal lock, `feature_voice_calls` (outbound blocked, inbound hang‑up), `feature_syndication` (create/syndicate/browse blocked). Inventory adapters: per‑connection, unchanged |
| T‑0017 | [x] | P1 | Confirmation guard for `generate_property_audit` | LLM path guarded; direct route = explicit request. Also fixed `PropertyAudit` field‑name bug (KI‑026) |
| T‑0016 | [x] | P1 | Operator go‑live checklist | `GO_LIVE_CHECKLIST.md` |

## M1 — Core foundation (Phase 1) — in progress ([MILESTONE_01](milestones/MILESTONE_01_CORE_FOUNDATION.md))

| ID | Status | P | Task | Notes |
|---|---|---|---|---|
| T‑1010 | [x] | P1 | OTP hashing (KI‑016) | ADR‑011. `OTPCode.code_hash` (keyed HMAC bound to phone+purpose), constant‑time check, `users 0013` hashes existing rows; admin no longer lists codes. 9 tests |
| T‑1020 | [x] | P1 | Inbound WhatsApp retry/dead‑letter under DB starvation (KI‑019) | ADR‑012. Retries with backoff on transient DB errors only when nothing was sent; `acks_late`; Redis dead‑letter + `replay_whatsapp_dead_letters`; webhook releases idempotency key on enqueue failure. 16 tests. Outbound outbox split to T‑1021 |
| T‑1021 | [x] | P1 | Transactional outbox for outbound side effects | ADR‑014. `OutboxMessage` (encrypted payload), on‑commit dispatch + relay + purge, lease/backoff/expiry, configurable queue. Adopted for OTPs (KI‑029 resolved) and notifications. Fixed KI‑033 (12 class‑level `WhatsAppClient` calls — nothing was being delivered). 26 tests |
| T‑1022 | [x] | P1 | Outbox adoption for remaining sends | Split a/b, both done. Deliberately direct: campaigns + AI re‑engagement (rate‑limited, per‑recipient state), lead `send-message`, chat replies, audit PDF document chain |
| T‑1022a | [x] | P1 | Generic `whatsapp.text` kind + alert/reminder sites | ADR‑014 addendum. `enqueue_whatsapp_text` (per‑site keys, org credentials at delivery, payload wiped after delivery, closed window → permanent). Adopted: hot‑lead alert, verification notice (fixed KI‑042 re‑send on every save), listing alerts, appointment/stale‑lead reminders, client re‑engagement (queued in the same transaction as the `*_sent_at` marker). 11 new tests, 14 updated |
| T‑1022b | [x] | P1 | Remaining direct sends | Escrow expiry notices (buyer/seller/agent, same transaction as the status change), seller deal‑lock code (after AML screening — fixed KI‑043), audit companion text, scraped‑listing follow‑up (expires with the 30‑min cache). Dead `notifications.services.send_whatsapp_message` removed; phone no longer logged in the follow‑up error. 5 tests |
| T‑1030 | [x] | P1 | Membership model consolidation plan (KI‑010) | ADR‑013 phase A: inventory, canonical `OrganizationMembership`, projection sync hooks, `organizations 0024` backfill, `reconcile_memberships`, deterministic primary org. Fixed stale access after team removal. Also KI‑032 (2 masking exemptions). 25 tests |
| T‑1031 | [x] | P1 | Policy‑based RBAC + active org context + permission‑matrix tests | ADR‑013 phase B. Split a/b |
| T‑1031a | [x] | P1 | Core policies on memberships, `X-Organization-ID` active org, grant policy, KI‑030, permission matrix | ADR‑020. `get_active_membership`; `IsAdminOrOrgAdmin`/`IsOrgAdmin`/`IsOrgMember` rewritten; legacy `use_membership_rbac` branch removed; `ActiveOrganizationMiddleware`; security engine accepts the selector and records foreign ones; owner never grantable, org_admin owner‑only. 22 tests |
| T‑1031b | [x] | P1 | Remaining legacy org reads moved to memberships | ADR‑020 addendum. New `get_admin_org` (owner/org_admin of active org) for management actions: billing, API keys, provisioning, org dashboards, team/agents, inventory, verification and WhatsApp developer scoping, compliance; `get_user_org` for member scoping, seat limits and the security auth context; `primary_org` for subject users in tasks/audit. Leads serializer fallback removed; `use_membership_rbac` default retired. Only projection writers still touch the legacy links. +4 tests |
| T‑1032 | [~] | P2 | Membership contract (ADR‑013 phase C) | Split a/b |
| T‑1032a | [x] | P2 | Membership service: invite, role change, removal, ownership transfer + audit | ADR‑021. Policy enforced in `membership.py`; `AuditLog` per change; `PATCH /me/members/{id}/`; `POST /me/ownership/transfer/` + platform‑admin variant; members view split (operationId collisions), OpenAPI documented (budgets 154/32 → 153/28). Also fixed KI‑040. 37 tests |
| T‑1032b | [ ] | P2 | Phase C schema contraction | Gated on a clean `reconcile_memberships` in prod: drop `AgentOrganizationMembership`, re‑key `AgentOrgMembership` on a membership, replace the `admin_user` one‑to‑one by the owner membership (then lift the "owns no other org" transfer rule), decide `Agent.organization`. Invite acceptance (pending state) needs a product decision first |
| T‑1033 | [ ] | P1 | PII‑masking middleware endpoint audit (KI‑032) | Decide per endpoint which JSON writes legitimately carry PII (lead/agent create, profile PATCH…); owner sign‑off on the policy |
| T‑1040 | [ ] | P2 | `is_sandbox` for unscoped leads (KI‑017) | after T‑1030 |
| T‑1011 | [ ] | P1 | MFA (TOTP) for staff and org admins | GAP‑082 |
| T‑1050 | [x] | P1 | `Market` entity; remove hardcoded PK defaults; UTC + per‑entity TZ | TR‑04. Subtasks a, b‑1, b‑2, c all done |
| T‑1050a | [x] | P1 | `Market` entity + market‑driven defaults + `market` FK expand/backfill + CI check | ADR‑019. `markets 0001/0002`, `properties 0023/0024`, `organizations 0025/0026`; `DEFAULT_MARKET`; 11 literal defaults → callables; `market_backfill_report`; admin; static check + ratchet (PKR 34 / PK fallback 20). 24 tests |
| T‑1050b | [x] | P2 | Move remaining PK logic literals to the market pack | Split 1/2, both done. Ratchet PKR 34 → 5 (the multi‑currency alias table), PK fallback 20 → 0 |
| T‑1050b‑1 | [x] | P2 | Country fallbacks + listing price formatting | `apps/markets/services.py`: `country_or_default`, `org_country`, `currency_for_country`, `format_price`. All 20 `or 'PK'` fallbacks gone (12 sites); price formatting in search results, AI search tool, `list_property` reply and AI verdict prompt uses the listing's currency (`PropertyResult.currency`, in the search `.only()` list). Fixed KI‑044. Ratchet PKR 34 → 27, PK fallback 20 → 0. 5 tests |
| T‑1050b‑2 | [x] | P2 | Remaining `'PKR'` literals | `apps/markets/pk/__init__.py` constants (`COUNTRY`, `CURRENCY`, `AREA_UNIT`) for code that is PK by design (audit engine, WhatsApp audit/listing tool contract, Safepay/bSecure, `FinancialConfig` defaults, NL search schema). Audit engine, PDF and WhatsApp audit summary label value‑derived money in `value_currency` and benchmark‑derived money in the benchmark currency (`market_analysis.benchmark_currency`); PDF via a context var. Lead budget + benchmark admin defaults market‑neutral. Fixed KI‑046; recorded KI‑045. 5 tests |
| T‑1050c | [x] | P1 | UTC + per‑entity time zone | ADR‑022. `TIME_ZONE`/`CELERY_TIMEZONE` = UTC; 3 crontabs converted to their former PKT instants (monthly report → hourly on UTC day 1 with per‑org local month); helpers `market_timezone`/`org_timezone`/`user_timezone`/`local_day_bounds`/`format_local`; report/stat trends bucket in the org's market zone (`tzinfo=`); "today" counts use local day bounds; user‑facing times local (KI‑047); report/certificate dates local. 10 tests. **Operator:** review DB `PeriodicTask` rows (checklist §12) |
| T‑1060 | [!] | P1 | Location hierarchy + PostGIS + geocoding backfill | Blocked: owner picks geocoding vendor; PostGIS in all envs |
| T‑1070 | [~] | P1 | Listing schema v2 (expand/contract) | ADR‑023; subtasks a–g (a–e done) |
| T‑1070a | [x] | P1 | Expand: purpose, rooms, floors, year built, parking, pets, price terms, `attributes` | Migrations 0025 (columns, `db_default`) + 0026 (concurrent index); API fields + list filters `purpose`/`min_bedrooms`/`min_bathrooms`; search + AI tool purpose/bedrooms; rent searches skip sale‑only scrapers (KI‑048). 16 tests |
| T‑1070b | [x] | P1 | Size rename `size_value`/`size_unit`/`size_m2` (+ `plot_size_m2`), dual‑write, migrate readers | Migrations 0027 (columns) + 0028 (batched backfill); `save()` dual‑write via load snapshot (`from_db`), changed pair wins, deferred loads → v1 wins, `update_fields` extended; `size_in()`/`size_display()`; `sync_listing_size` drift command; readers moved (search band on m², scoring, alerts, AI score prompt, copilot incl. bedrooms, seed); API `area_*` deprecated aliases, conflicts → 400 (KI‑049). 16 tests |
| T‑1070c | [x] | P1 | `PriceHistory` | Migrations 0029 (table) + 0030 (idempotent baseline per priced listing, dated at creation). `Property.save()` appends a row in the same transaction when price/currency/price_type/price_on_request change (load snapshot; deferred loads compare with the last row; `update_fields` without price → skip). Attribution via `price_change_context(user, source)` in API create/update, admin, AI listing tool. `GET properties/{id}/price-history/` (listing visibility; actor not exposed). 10 tests |
| T‑1070d | [x] | P1 | Category/subtype taxonomy + amenities | ADR‑023 addendum. `category`/`subtype` (code taxonomy in `taxonomy.py`), `property_type` synced deprecated alias (snapshot rule, `update_fields` extended); `Amenity` (labels per language, markets) + `ListingAmenity`; migrations 0031 (expand), 0032 (backfill), 0033 (seed 35, en+ur), 0034 (concurrent index); `sync_listing_taxonomy` drift command. API: read/write by key (market check, ≤60), filters `category`/`subtype`/`amenities` (all match, ≤10), `GET properties/taxonomy/`; search service + AI tool amenity hard filter (unknown keys dropped, scrapers skipped); admin. 23 tests |
| T‑1070e | [x] | P1 | `Market.listing_schema` validates `attributes` | ADR‑023 addendum. In‑house schema format + validator (`apps/markets/listing_schema.py`); `markets 0003` (column, `db_default`) + `0004` (PK/AE/GB seeds, fill‑if‑empty); `Market.clean` checks the schema; `Property.clean` + serializers check values (only when `attributes`/`country` written); `MarketInfo.listing_schema`, cache prefix v2. Recorded KI‑050/051. 9 tests |
| T‑1070f | [ ] | P2 | Stored `tsvector` + trigger | KI‑035 |
| T‑1070g | [ ] | P1 | Contract: drop `area_marla`/`area_unit`; minor‑unit money decision | **Owner approval** (destructive) |
| T‑1080 | [ ] | P2 | Listing lifecycle + transitions + expiry | after T‑1070 |
| T‑1090 | [x] | P2 | API standards: OpenAPI, error envelope, filter validation, idempotency keys | All subtasks a–d done (ADR‑016/017/018). Follow‑ups T‑1091/T‑1092 |
| T‑1090a | [x] | P2 | OpenAPI 3.1 schema | ADR‑016. `drf-spectacular==0.29.0`; `GET /api/v1/schema/` (staff/admin unless `OPENAPI_SCHEMA_PUBLIC`); JWT header/cookie + org API‑key schemes; CI `spectacular --validate` + `openapi-schema` artefact; error/warning budget ratchet (154/32 unique). 11 tests |
| T‑1090b | [x] | P2 | Consistent error envelope | ADR‑017. `RequestIdMiddleware` (header, log field, CORS); additive `error_info {status, code, message, fields}` + `request_id` on every `/api/` JSON error via the exception handler + `ErrorEnvelopeMiddleware`; JSON `handler404/500` for `/api/`; schema component. 20 tests |
| T‑1090c | [x] | P2 | Query‑param filter validation | `apps/core/query_params.py` (`int_param`, `limit_offset`, `choice_param`); 9 bare `int()` casts in 7 views replaced (negative limits were 500s; chain‑verify `limit` now capped at 10 000); property list: enum `type`/`legal_status`, `min_price ≤ max_price`, `min_score ≤ 100`, `ref_no` exact upper‑case (KI‑036), parameters in OpenAPI. 18 tests |
| T‑1090d | [x] | P2 | `Idempotency-Key` for enquiry/payment/booking POSTs | ADR‑018. `core.IdempotencyRecord` (encrypted body, lease, TTL) + `core 0003`; `@idempotent` on lead/appointment create, checkout, SEPA, deal‑lock initiate; hourly purge; CORS; OpenAPI header. 15 tests |
| T‑1092 | [ ] | P3 | Replace hand‑built `Response({'error': …}, status=4xx)` with raised DRF exceptions carrying codes | KI‑038; `error` string stays for compatibility |
| T‑1091 | [ ] | P3 | Annotate the 153 schema‑less `APIView`s (`serializer_class`/`@extend_schema`); fix the remaining operationId collisions (members fixed in T‑1032a) | KI‑037; lower the budgets in `tests/test_openapi_schema.py` as they go |
| T‑1100 | [x] | P2 | Search indexes (composite, GIN, trigram) | ADR‑015. `pg_trgm`; 4 concurrent indexes (FTS GIN, trigram on `UPPER(city)`/`UPPER(location)`, partial rank B‑tree); index‑backed `?search=` (prefix/AND, `simple` config, ref‑no exact). 200k‑row EXPLAIN: no seq scans; selective FTS 631 → 3 ms. 28 tests. GiST/geo stays with T‑1060 |
| T‑1110 | [ ] | P2 | Media pipeline hardening for images | |
| T‑1120 | [ ] | P2 | AI provider abstraction, prompt registry, market‑pack prompts | |

## M2 — AI agent MVP — planned entries referenced by ADRs

| ID | Status | P | Task |
|---|---|---|---|
| T‑2010 | [ ] | P1 | Explicit tool loop for Gemini (accurate per‑round usage, tool auditing) |
| T‑2012 | [ ] | P1 | Durable `ActionConfirmation` record (replace cache‑only proposals) |
| T‑2014 | [ ] | P2 | Listing draft/moderation state (KI‑025) |
| T‑2016 | [ ] | P2 | Context‑free FAQ path so the semantic cache can be enabled safely |
