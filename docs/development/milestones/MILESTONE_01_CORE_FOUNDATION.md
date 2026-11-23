# M1 — Core Platform Foundation (PRD Phase 1)

**Goal:** build a domain model and an access model that can carry a global product. Source: [PRODUCT_ROADMAP § Phase 1](../../product/ai-real-estate-agent/PRODUCT_ROADMAP.md).

**Started:** 2026‑11‑21 (session 4). M0 is code‑complete; it closes when the first GitHub Actions run is green.

## Exit criteria (from the PRD)
| Criterion | State |
|---|---|
| All existing listings migrated to schema v2, with a backfill report and < X % unmatched locations | In progress: T‑1070a expand done (existing rows backfilled to purpose=sale); size rename done (T‑1070b, backfilled); category taxonomy done (T‑1070d, backfilled from `property_type`); location tree + listing link + coverage report done (T‑1060a, `match_listing_locations`); PostGIS point and geocoding pending (T‑1060b/c) |
| Permission matrix tests pass for every role | **In progress**: org‑admin/members/leads matrix for 8 actors + active‑org switching (T‑1031a); management endpoints vs. selected viewer org (T‑1031b); member invite/role‑change/removal/transfer rules (T‑1032a) |
| OpenAPI published | **Met in CI** (T‑1090a): validated 3.1 schema published as a CI artefact and at `/api/v1/schema/` for staff. Coverage debt: KI‑037 |
| No hardcoded `PKR`/`PK`/`marla` defaults outside the PK market pack (grep check in CI) | **Met for defaults** (T‑1050a): CI test rejects literal market defaults; logic literals ratcheted (T‑1050b) |

## Tasks
| ID | P | Deliverable | PRD ref | Depends on |
|---|---|---|---|---|
| T‑1010 ✅ | P1 | OTP hashing | TR‑13, KI‑016 | — |
| T‑1011 | P1 | MFA (TOTP) for staff and org admins | GAP‑082 | T‑1010 |
| T‑1020 ✅ | P1 | Inbound WhatsApp retry + dead‑letter under DB starvation | KI‑019 | — |
| T‑1021 ✅ | P1 | Transactional outbox for outbound side effects + named queues (ADR‑014) | GAP‑043, TR‑09 | T‑1020 |
| T‑1022 ✅ (a, b) | P1 | Outbox adoption for the remaining outbound sends: alerts/reminders, escrow, audit companion, scraped follow‑up (`whatsapp.text`); campaigns stay rate‑limited direct | GAP‑043 | T‑1021 |
| T‑1030 ✅ | P1 | Membership consolidation plan (ADR‑013) + phase A sync/backfill | GAP‑015, KI‑010 | — |
| T‑1031 ✅ | P1 | Policy‑based RBAC + active org context + permission‑matrix tests (ADR‑013 phase B) | TR‑06 | T‑1030 |
| T‑1032 (a ✅, b) | P2 | Membership contract: audited service + ownership transfer (ADR‑021) ✅; phase‑C schema contraction (ADR‑013) | KI‑010 | T‑1031; b: clean `reconcile_memberships` in prod |
| T‑1033 | P1 | PII‑masking endpoint audit | KI‑032 | owner policy sign‑off |
| T‑1040 | P2 | `is_sandbox` semantics for unscoped leads | KI‑017 | T‑1030 |
| T‑1050 ✅ (a, b, c) | P1 | `Market` entity from the registry; remove hardcoded PK defaults; UTC + per‑entity TZ | TR‑04, GAP‑014/091 | — |
| T‑1060 | P1 | Location hierarchy + PostGIS point + geocoding backfill with a review queue | GAP‑013/041 | T‑1050; **owner:** geocoding vendor, PostGIS in all envs |
| T‑1070 (a ✅, b ✅, c ✅, d ✅, e ✅, f ✅; g needs approval) | P1 | Listing schema v2 (expand/contract, ADR‑023) | GAP‑010/012/045 | T‑1050 |
| T‑1080 | P2 | Listing lifecycle, transitions, audit, expiry | GAP‑011 | T‑1070 |
| T‑1090 ✅ (a–d) | P2 | API standards: OpenAPI ✅, error envelope, filter validation, idempotency keys | GAP‑030–032 | — |
| T‑1100 ✅ | P2 | Search indexes (composite, GIN, trigram) | GAP‑040 | — |
| T‑1110 | P2 | Media pipeline hardening for images | GAP‑083 | — |
| T‑1120 | P2 | AI provider abstraction, prompt registry, market‑pack prompt fragments | GAP‑055/056/062 | — |

## Order
T‑1010 ✅ → T‑1020 ✅ → T‑1030 ✅ → T‑1021 ✅ → T‑1100 ✅ → T‑1090 ✅ → T‑1050 ✅ → T‑1031 ✅ → T‑1032a ✅ → T‑1022 ✅ → T‑1070 (a–f ✅) → T‑1033 ✅ → T‑1032c ✅ → T‑1060a ✅ → T‑1070g‑1 ✅ (g‑2 staged for the next release) → T‑1060b ✅ (geo search; PostGIS optional) → T‑1060c ✅ (Google, flag off) → T‑1080 ✅ (lifecycle; review/TTL per market, off) → T‑1011 → T‑1110 → T‑1120.
