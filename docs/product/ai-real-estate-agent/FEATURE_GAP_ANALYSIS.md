# Feature Gap Analysis

| | |
|---|---|
| **Version** | 1.0 · 2026-11-21 |
| **Baseline** | `pakpropai` @ `0b574a7` |
| **Related** | [EXISTING_SYSTEM_ASSESSMENT](EXISTING_SYSTEM_ASSESSMENT.md) (evidence) · [PRD_MASTER](PRD_MASTER.md) (requirements) · [PRODUCT_ROADMAP](PRODUCT_ROADMAP.md) (sequencing) |

Status vocabulary follows the assessment: OPERATIONAL · IMPROVE · PARTIAL · PROTOTYPE · PLANNED · MISSING · DEPRECATE · DEFECT. Priorities P0–P3 as defined in [PRD_MASTER §C.0](PRD_MASTER.md#c0-prioritisation-rationale). Complexity: **S** (≤1 engineer‑week), **M** (1–3), **L** (3–8), **XL** (>8).

---

## 1. Gap table by area

### 1.1 Product & user experience dependencies

| ID | Capability | Existing state | Required state | Gap | Pri | Recommended action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑001 | Consumer AI channel | WhatsApp + voice only | Web/app chat API + WhatsApp on one runtime | No consumer REST/stream chat | P1 | Build conversation API (FR‑AI‑001) reusing `AIServiceManager` | L |
| GAP‑002 | Public discovery | All property APIs require auth | Public, cacheable listing search/detail for SEO and anonymous browsing | Missing public read API | P1 | Add `public/listings` read endpoints with strict field allow‑list | M |
| GAP‑003 | Saved searches & alerts | Missing | Saved criteria, alert frequency, channel | Missing | P1 | New `SavedSearch`, matcher job, notifications | M |
| GAP‑004 | Favourites/shortlist | Missing | Per‑user lists with notes | Missing | P1 | New entity + API | S |
| GAP‑005 | Comparison for consumers | Agent‑only raw compare | Consumer compare with normalised table | Partial | P1 | Extend `PropertyCompareView`; fix route order | S |
| GAP‑006 | Frontend dependency | Next.js repo exists (not assessed) | Consumer chat, map search, listing pages, agent inbox | Unknown | P1 | Assess `pakpropaiweb` separately | — |

### 1.2 Backend / domain

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑010 | Listing core attributes | No purpose/beds/baths/amenities | Full core schema (FR‑LST‑001) | Fundamental | **P0** | Listing schema v2 via expand/contract migrations | L |
| GAP‑011 | Listing lifecycle | `is_active` only | Draft→review→published→…; expiry | Missing | **P0** | State machine service + audit | M |
| GAP‑012 | Pricing model | Single BigInteger price | Price type/period, deposit, service charge, history | Partial | P0/P1 | `ListingPrice` fields + `PriceHistory` | M |
| GAP‑013 | Location model | Free‑text city/location; lat/lon decimals | Location hierarchy + PostGIS point | Missing | **P0** | `Location` tree, geocoding on save, backfill | L |
| GAP‑014 | Market configuration | Registry dataclass (4 markets) + scattered PK defaults | `Market` entity driving defaults, schemas, disclaimers | Partial | **P0** | Promote registry to DB‑backed config + code plugins | M |
| GAP‑015 | Membership/RBAC | 3 membership tables + legacy FKs; hardcoded role checks | Single membership + policy‑based permissions | Partial | **P0** | Consolidate to `OrganizationMembership`; permission registry | L |
| GAP‑016 | Unified messaging | `ConversationMessage` + `WhatsAppMessage` | One `Message` model with channel | Duplicate | P1 | Consolidate with a migration view | M |
| GAP‑017 | CRM pipeline | Fixed status + routing state | Configurable pipeline, tasks | Partial | P1 | Add `PipelineStage`, `Task` | M |
| GAP‑018 | Appointments | CRUD + reminders | Availability, slot booking, TZ, feedback | Partial | P1 | Availability rules + slot service | M |
| GAP‑019 | Offers | Missing | Offer register | Missing | P2 | New entity | S |
| GAP‑020 | Projects/developments | Missing | Project with units | Missing | P2 | New entities | M |

### 1.3 APIs

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑030 | API contract | No schema | OpenAPI 3.1, generated & published | Missing | P1 | Add schema generation (package addition = implementation decision) | S |
| GAP‑031 | Error/validation consistency | Bare `int()` casts → 500 | Field‑level 400s, error envelope | Defect | P1 | Filter classes/serializers for query params | S |
| GAP‑032 | Idempotency | Webhooks only | `Idempotency-Key` on payment/booking/enquiry POSTs | Missing | P1 | Middleware + store | S |
| GAP‑033 | Identifier consistency | Mixed UUID/int | UUID public IDs | Inconsistent | P2 | Add `public_id` UUID to int‑PK models | M |
| GAP‑034 | Route shadowing | `properties/compare/` after router `r''` | Explicit routes before router | Probable defect | P0 | Add test; reorder | S |
| GAP‑035 | Pagination | Page number | Cursor for feeds/search | Partial | P2 | Cursor pagination on high‑volume lists | S |

### 1.4 Database

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑040 | Search indexes | Single‑column B‑tree | Composite, GIN tsvector, trigram, GiST | Missing | P1 | Index migration (concurrent) | S |
| GAP‑041 | Geospatial | None | PostGIS geography + GiST | Missing | P1 | Enable extension; migrate lat/lon | M |
| GAP‑042 | Vector storage | None (embeddings only in Redis cache) | pgvector for knowledge/listings | Missing | P2 | Enable extension | S |
| GAP‑043 | Outbox/events | None | Transactional outbox | Missing | P1 | `OutboxEvent` + dispatcher | M |
| GAP‑044 | Log growth | Celery results in DB; AI logs unbounded | Retention and partitioning | Missing | P2 | Retention jobs; partition by month | S |
| GAP‑045 | Misnamed fields | `area_marla` | `size_value` | Debt | P0 | Expand/contract rename | S |

### 1.5 AI

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑050 | Tool loop control | Gemini AFC (SDK‑managed) | Platform‑managed loop with policies | Architectural | **P0** | Implement explicit loop (AI spec §3) | M |
| GAP‑051 | Side‑effect confirmation | Prompt‑only | Server‑side confirmation tokens | Missing | **P0** | `PendingAction` + confirm endpoint/WA reply | M |
| GAP‑052 | Token/cost accounting | Records 0/0 | Real usage from provider responses | Defect | **P0** | Capture `usage_metadata`; cost table | S |
| GAP‑053 | Semantic cache scope | Org‑scoped for any reply | Only non‑personal FAQ intents; versioned | Privacy defect | **P0** | Restrict or disable pending redesign | S |
| GAP‑054 | Output grounding | `ComplianceEngine` unwired | Grounding check on every property claim | Partial | **P0** | Wire + extend to beds/baths/amenities | M |
| GAP‑055 | Prompt management | Constants in code | Versioned prompt registry, per‑market overrides | Missing | P1 | `PromptTemplate`/`PromptVersion` | M |
| GAP‑056 | Model failover | Canned fallback | Secondary provider/model | Missing | P1 | Provider abstraction w/ routing | M |
| GAP‑057 | Structured outputs | Pydantic for intents; free text replies | Structured criteria + response envelope | Partial | P1 | JSON schema outputs | M |
| GAP‑058 | Memory | Redis 24 h | Durable conversation + consented profile | Partial | P1 | New models | M |
| GAP‑059 | RAG | None | Knowledge retrieval with citations | Missing | P2 | pgvector + ingestion | L |
| GAP‑060 | Evaluation | Offline harness | Golden sets + CI gate + online feedback | Prototype | P1 | Extend `apps/ml` | M |
| GAP‑061 | Guardrails | Regex | Layered: regex + classifier + policy + indirect injection handling | Partial | P1 | AI spec §8 | M |
| GAP‑062 | Market neutrality | PK hardcoding in prompts/bypass | Market packs supply prompts/keywords | Debt | P1 | Move to market packs | M |
| GAP‑063 | AI observability | `AIInteraction` mislabels | Per‑call trace incl. prompt version, tools, cost | Partial | P0 | Extend logging | S |

### 1.6 Search & property data

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑070 | Listing supply legality | Scraping Zameen/Graana | First‑party + licensed feeds only | Critical | **P0** | Disable scrapers (`scraper_enabled` false) → remove from path; legal review | S |
| GAP‑071 | Ranking | `-ai_score, -created_at` | Configurable factor ranking with explanations | Missing | P1 | Ranking service | M |
| GAP‑072 | Sandbox leakage | Not filtered | Strict visibility rules | Defect | **P0** | Add filters + tests | S |
| GAP‑073 | Dedup | Query‑time heuristic | Persistent clustering | Prototype | P2 | Fingerprint job | M |
| GAP‑074 | POI/neighbourhood data | `NeighbourhoodZone` seed | Licensed POI for proximity | Missing | P2 | Vendor selection | M |
| GAP‑075 | FX rates | None | Daily snapshots | Missing | P1 | FX job + table | S |
| GAP‑076 | AI property score | LLM opinion nightly for all listings | Deterministic quality score; LLM commentary optional | Redesign | P1 | Replace `ai_score` in ranking with quality score | M |

### 1.7 Security & privacy

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑080 | Secrets at rest | Plaintext DB columns | Encrypted with managed keys; rotation | Critical | **P0** | Encrypted fields + re‑encrypt migration | M |
| GAP‑081 | National ID / OCR text | Plaintext | Encrypted, minimised, retention | High | **P0** | Encrypt; purge `raw_ocr` after review | M |
| GAP‑082 | MFA | None | TOTP for staff/org admins | Missing | P1 | Add | M |
| GAP‑083 | Upload security | MIME + size | Sniffing, EXIF strip, malware scan, private bucket | Partial | P1 | Media pipeline | M |
| GAP‑084 | Repo hygiene | Committed PDFs, vector DB, agent tool state | Clean repo; history scrub if PII found | Debt | P0 | Review and remove (owner decision) | S |
| GAP‑085 | CSRF with cookie JWT | Configured | Verified by tests | Unknown | P1 | Add tests | S |
| GAP‑086 | Fairness / non‑discrimination | Not addressed | Policy + evals | Missing | P1 | AI spec §8.5 | M |
| GAP‑087 | Legal adequacy of privacy | Scaffolding | Per‑market review | Unknown | P0 (launch market) | Counsel review | — |

### 1.8 Performance, scalability, globalisation

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑090 | Nightly LLM rescoring | All properties nightly | Event‑driven, only on change | Cost/scale | P1 | Score on create/update | S |
| GAP‑091 | Time zones | Server & Celery `Asia/Karachi` | UTC + per‑entity TZ | Debt | P1 | Migrate | M |
| GAP‑092 | Localisation of content | Hardcoded English/Roman Urdu strings | i18n catalogues per market | Partial | P1 | Extract strings | M |
| GAP‑093 | Data residency | Router + optional aliases | Only when contracted | Over‑built | P3 | Keep dormant | — |

### 1.9 Monetisation & operations

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑100 | Plans | Enum + Stripe price IDs in config | Plans/entitlements as data | Partial | P1 | `Plan`, `Entitlement` | M |
| GAP‑101 | Usage metering | Broken token data | Metered AI conversations, seats, listings | Missing | P2 | After GAP‑052 | M |
| GAP‑102 | Moderation tooling | Verification queue | Listing/user/message moderation | Partial | P1 | Moderation module | M |
| GAP‑103 | AI ops console | None | Prompts, models, evals, kill switch | Missing | P1 | Admin APIs | M |
| GAP‑104 | Unified audit log | 3 overlapping logs | One audit trail + security chain | Partial | P1 | Consolidate | M |

### 1.10 Testing & deployment

| ID | Capability | Existing | Required | Gap | Pri | Action | Cx |
|---|---|---|---|---|---|---|---|
| GAP‑110 | CI correctness | Paths assume parent monorepo; missing `infra/scripts/health_check.py`; frontend build context absent | Green pipeline on this repo | Defect | **P0** | Fix paths or move workflow to the monorepo root | S |
| GAP‑111 | Test baseline | 1,435 tests, not run here | Known pass rate + coverage per module | Unknown | **P0** | Run suite in CI; record baseline | S |
| GAP‑112 | AI regression | None in CI | Eval gate on prompt/model change | Missing | P1 | Eval job | M |
| GAP‑113 | Chaos findings | DB‑starvation buffering/retries failed | Fixed or accepted | Open | P2 | Triage | M |
| GAP‑114 | Docs | README drift | Accurate runbooks | Debt | P1 | Update README | S |
| GAP‑115 | Staging parity | Smoke tests reference secrets/hooks | Staging with seeded markets | Partial | P1 | IaC + seed | M |

---

## 2. Classification summaries

### 2.1 Critical blockers (must fix before any public launch)

GAP‑070 scraping · GAP‑080/081 plaintext secrets & IDs · GAP‑053 semantic cache leakage · GAP‑050/051 uncontrolled side‑effect tools · GAP‑072 sandbox leakage · GAP‑052 cost accounting · GAP‑110/111 CI & test baseline · GAP‑087 launch‑market legal review · GAP‑010/011 listing schema & lifecycle.

### 2.2 High‑risk architectural gaps

Location model and PostGIS (GAP‑013/041) · RBAC consolidation (GAP‑015) · AI orchestration (GAP‑050) · Market entity (GAP‑014) · Unified messaging (GAP‑016).

### 2.3 Missing foundational capabilities

Conversation API, durable memory, saved searches/alerts, ranking service, prompt registry, eval gating, outbox, OpenAPI.

### 2.4 Features that require redesign

`ai_score` as a ranking signal (LLM opinion) → deterministic quality score · `PropertyAudit` → "Indicative Price Context" with provenance and disclaimers · `market-trends` → labelled asking‑price trends · agent‑request keyword bypass → tool policy · greeting menu → market‑pack driven.

### 2.5 Unnecessary for MVP (freeze behind flags, do not delete)

Voice agent · Deal Lock/escrow payments · syndication marketplace & commission ledger · regional DB clusters · AML sanctions sync (unless required for launch‑market agents) · fine‑tuning dataset curation · chaos engineering expansion · external inventory adapters other than ones with signed agreements.

### 2.6 Requiring external vendors

LLM providers (2+) · WhatsApp BSP/Meta · email/push · maps/geocoding/POI · FX rates · IDV · OCR · payments · calendars · telephony.

### 2.7 Requiring licensed datasets

Third‑party listings (portal feeds) · transaction prices · rental indices · POI/schools/transit (some open data exists, licences vary) · neighbourhood statistics · property records/title registries.

### 2.8 Legally or operationally complex

Escrow/token payments · valuation‑like outputs · tenant screening · fair‑housing/anti‑discrimination · cross‑border data transfers · real‑estate advertising rules (permit numbers) · recording of voice calls · WhatsApp marketing opt‑in rules · lead resale.

---

## 3. Feature priority register

| Priority | Features (IDs) | Why |
|---|---|---|
| **P0** | Secrets/PII encryption (GAP‑080/081); scraping removal (070); sandbox filtering (072); semantic cache fix (053); tool loop + confirmations (050/051); token accounting + AI tracing (052/063); grounding check wired (054); CI + test baseline (110/111); listing schema v2 + lifecycle (010/011/012 core/045); location hierarchy (013); Market entity (014); RBAC consolidation (015); route fix (034); repo hygiene (084); valuation disclaimers on existing audits (FR‑VAL‑004) | Security, legality, data correctness and foundations everything else depends on |
| **P1** | Conversation API (001); public listing API (002); saved searches/alerts (003); favourites (004); consumer compare (005); search indexes + PostGIS (040/041); ranking + explanations (071); FX (075); quality score (076); prompt registry (055); failover (056); structured outputs (057); memory/profile (058); eval gating (060/112); layered guardrails (061); market‑neutral prompts (062); CRM pipeline/tasks (017); appointments/slots (018); unified messaging (016); email notifications; listing assistant; moderation (102); AI ops console (103); plans as data (100); unified audit (104); MFA (082); upload security (083); fairness policy (086); OpenAPI (030); validation (031); idempotency (032); outbox (043); TZ (091); i18n (092); event‑driven scoring (090) | Delivers the MVP loop in one market safely |
| **P2** | RAG (059); vector storage (042); POI proximity (074); persistent dedup (073); offers (019); projects (020); AI comparison report; indicative price context; investment calculator; document intelligence; push/SMS; calendar sync; usage metering (101); featured listings; LTR recommendations; cursor pagination (035); public IDs (033); log retention (044); chaos fixes (113) | Requires usage data, partner data or P1 adoption |
| **P3** | Voice agent; Deal Lock/escrow; syndication; neighbourhood assistant; transaction milestones; tenant screening; international relocation; partner services marketplace; regional clusters (093); commute search; consumer premium; transaction‑based valuation | Regulatory complexity, licensed data, or speculative return |

---

## 4. Retain / enhance / redesign / build / freeze summary

| Module | Decision |
|---|---|
| users, organizations, core middleware, security, observability, resilience | **Retain + enhance** |
| leads, agents | **Retain + enhance** (pipeline, tasks, consolidation) |
| properties | **Redesign the data model; retain ownership logic** |
| ai | **Enhance → partial redesign** of orchestration (keep guardrails, classifier, context builder, market packs) |
| whatsapp | **Retain**; becomes a channel adapter to the shared runtime |
| markets | **Enhance** into the Market configuration layer |
| verification, compliance | **Retain + harden** |
| reports, notifications, campaigns | **Retain + extend** |
| billing | **Enhance** (plans as data, metering) |
| inventory | **Retain framework; enable adapters only with agreements** |
| audit (property audit) | **Redesign** as indicative price context |
| escrow, payments (deal lock), marketplace, voice, ml, infra regional | **Freeze behind flags** (P3) |
| properties/scrapers | **Remove from production path** (owner to decide on deletion after legal review) |
