# MVP Scope

| | |
|---|---|
| **Version** | 1.0 · 2026-11-21 |
| **Related** | [PRD_MASTER](PRD_MASTER.md) · [PRODUCT_ROADMAP](PRODUCT_ROADMAP.md) (Phases 0–2, plus the minimum of 3) · [RISKS_AND_DEPENDENCIES](RISKS_AND_DEPENDENCIES.md) |

## 1. MVP thesis

> In one launch market, a consumer can describe what they need in natural language (web or WhatsApp), get **lawfully sourced, de‑duplicated, explained** property matches, save and compare them, and send a **consented enquiry** that reaches a **verified agent** who answers quickly, with AI help, and books a viewing.
>
> Agencies pay for this because it answers every lead instantly and hands them qualified prospects.

The MVP **is not** a feature catalogue. Many existing modules are **frozen**, not deleted (see §4).

## 2. Launch market recommendation

- **Primary:** Pakistan, in 1–3 cities chosen with launch agencies (`ASSUMPTION:` cities where the first paying agencies operate).
- **Secondary readiness (not launched in MVP):** UAE configuration completed and tested in staging, to prove the market layer works.
- **Why:** Pakistan has the deepest existing implementation (market pack, units, calculators, Roman‑Urdu handling, local gateways), and the WhatsApp‑first design matches it. Building on the `Market` entity keeps the product global‑ready. GB/US are deferred because of data licensing and stricter advertising and fair‑housing regimes.
- **Validation needed:** customer discovery interviews with 15–30 agencies/developers before Phase 2 ends; a legal review of the launch market.

## 3. In scope (MUST)

| # | Capability | Requirement IDs | Built on |
|---|---|---|---|
| M1 | **Stabilisation blockers fixed:** secrets encrypted, scraping off, sandbox excluded, semantic cache scoped, token accounting real, side‑effect tools confirmed, CI green with a known test baseline | GAP‑050–053, 070, 072, 080–081, 110–111 | Phase 0 |
| M2 | Market entity (PK live, AE staging), location hierarchy for launch cities, FX display | FR‑SRCH‑009, TR‑04 | Phase 1 |
| M3 | Listing schema v2: purpose, beds/baths, size, amenities, price type/period, lifecycle with moderation, images hardened | FR‑LST‑001…009, 016 | Phase 1 |
| M4 | RBAC consolidation: consumer, private lister (if the market permits), agent, team manager, org admin, moderator, platform admins | TR‑06 | Phase 1 |
| M5 | Hybrid search (filters + PostGIS radius/bbox + full‑text), explainable ranking, public listing read API | FR‑SRCH‑001…008, FR‑REC‑001/003 | Phase 2 |
| M6 | AI discovery agent on the new runtime, on **web chat and WhatsApp**: NL→criteria, clarifying questions, refinement, grounded listing Q&A, explanations, escalation | FR‑AI‑001…011 | Phase 2 |
| M7 | Favourites, saved searches with email/WhatsApp alerts, basic comparison (2–4 listings) | FR‑REC‑004, FR‑CMP‑001/002 | Phase 2 |
| M8 | Consented enquiries → Lead (existing routing/auto‑assign) → agent notification; in‑app thread + WhatsApp | FR‑COM‑001/002, FR‑CRM‑003 | Phase 2 |
| M9 | Viewing booking: agent availability rules, slot booking (AI + UI, with confirmation), reminders, post‑viewing feedback | FR‑APT‑001…006 | Phase 2 |
| M10 | Agent workspace minimum: lead inbox, conversation summary + suggested replies (existing), take‑control (existing), tasks/follow‑ups, response‑time metrics | FR‑CRM‑004/006/008 | Phase 2–3 |
| M11 | AI listing assistant minimum: grounded description generation + missing‑info detection | FR‑LAI‑001/002 | Phase 3 (pulled into MVP) |
| M12 | AI governance minimum: prompt registry, AI traces with cost, eval suites for extraction/grounding/safety in CI, failover provider, kill switch | AI spec §9–11 | Phases 0–2 |
| M13 | Org subscriptions (existing Stripe/local) with plan limits for seats, listings and AI conversations | GAP‑100 (minimum) | Phase 3 (minimum) |
| M14 | Privacy minimum: consent per purpose (contact share, AI memory, marketing), data export, deletion across conversations/memory, disclaimers per market | Part E | Phases 0–2 |
| M15 | Moderation minimum: listing review queue, user reports, takedown | G2 | Phase 2 |

## 4. Out of scope for MVP

### 4.1 Deferred (planned later)

| Capability | Target phase | Reason |
|---|---|---|
| RAG knowledge assistant | 4 | Static market pack is enough for launch FAQs |
| AI comparison narrative, indicative price context | 4 | Needs grounding maturity and data |
| Investment calculator + commentary (beyond existing tax/loan routes) | 4 | Not core to the first loop |
| Document intelligence with a real OCR backend | 4 | Human verification is sufficient at launch |
| POI proximity search ("near metro") | 4 | Requires licensed POI data |
| Persistent duplicate clustering | 4 | Low volume at launch; moderation covers it |
| Push notifications, SMS fallback, calendar sync | 3–4 | Email + WhatsApp are sufficient |
| Offers register, projects/units | 3–4 | Developer segment is a later focus |
| Usage‑based billing, featured listings | 3–5 | After pricing validation |
| Learned ranking | 4+ | Needs interaction data |
| Second market live | 5 | After primary market traction |

### 4.2 Do not build yet (frozen behind feature flags)

| Existing module | Action | Reason |
|---|---|---|
| Deal Lock / escrow token payments (`escrow`, `payments` deal‑lock flows, `initiate_deal_lock` tool) | Disable the tool; flag off endpoints | Potentially regulated escrow/payment activity; needs a licensed partner and a legal opinion |
| Portal scrapers (`properties/scrapers`) | Remove from the search path; disable | Terms/licensing risk |
| Voice AI (`voice`) | Flag off | Cost, consent/recording law, not core |
| Syndication marketplace + commission ledger | Flag off | Developer/broker network is a later bet |
| Regional DB clusters | Leave dormant | No residency requirement yet |
| Fine‑tuning dataset curation | Dormant | Prompting + RAG first |
| External inventory adapters without signed agreements (Rightmove, Bayut, Zameen feeds) | Disabled per connection | Partner access not verified |
| Property audit PDFs | Keep only with provenance labels + "not a valuation" disclaimer, or disable | Valuation‑claim risk |

## 5. Dependencies

| Dependency | Type | Needed by |
|---|---|---|
| Launch‑market legal review (privacy, advertising, consumer protection, AI disclosures) | Business | Before public beta |
| 5–10 launch agencies with real listings (supply) | Business | Phase 2 beta |
| WhatsApp Business accounts per agency (existing provisioning) | Vendor | Phase 2 |
| Secondary LLM vendor contract | Vendor | Phase 2 |
| Maps/geocoding vendor | Vendor | Phase 1 |
| Email provider | Vendor | Phase 2 |
| KMS / secret manager | Infra | Phase 0 |
| Postgres with PostGIS (and later pgvector) in all environments | Infra | Phase 1 |
| Frontend (Next.js repo) delivering chat, search, listing pages and agent inbox | Engineering | Phase 2 |
| Golden eval datasets (English, Urdu, Roman Urdu) | Data | Phase 2 |
| Moderation staff | Operations | Beta |

## 6. MVP acceptance criteria

**Functional**
1. A consumer can complete *discover → shortlist → enquire → book viewing* on web and on WhatsApp without staff intervention. Verified by E2E tests in staging with seeded markets.
2. The same `SearchCriteria` returns identical results via the search API and the AI tool.
3. Every listing shown is `published`, in a live market, not sandbox, and from a first‑party or licensed source.
4. Agents receive enquiries within 1 minute of creation (notification delivery measured) and can reply from the workspace or WhatsApp.
5. Saved‑search alerts deliver new matches within the configured frequency.

**AI quality**
6. Criteria extraction F1 meets the threshold set in Phase 2 eval baselining, for each launch language.
7. **Zero critical hallucinations** (listing facts absent from source) in the release eval suite; the grounding checker is active in production.
8. Side‑effect tools (enquiry, booking, saved search, listing draft) never execute without a confirmation record. Covered by automated tests.
9. AI cost per conversation and per qualified lead is visible in dashboards using real token data.

**Security & privacy**
10. No plaintext secrets or national IDs in the database (verified by a schema audit and a data scan).
11. A data export and deletion request covers conversations, memory, enquiries and messages, and completes within the defined SLA.
12. Pentest on the public API and chat surface, with no open high/critical findings.

**Operational**
13. CI green: tests, migrations check, security scans, eval gate. Staging deploys automatically and production uses a manual approval.
14. SLO dashboards and alerts are live for API, search, AI reply and webhook processing.
15. Runbooks exist for LLM outage, WhatsApp token expiry, data incidents and harmful AI output.

## 7. Launch readiness checklist

- [ ] Legal sign‑off for the launch market (privacy notice, terms, AI disclosure, listing advertising rules)
- [ ] Listing supply: ≥ N published listings per launch city (`ASSUMPTION:` N agreed with sales; enough that typical queries return results)
- [ ] Moderation staffing and SLA defined
- [ ] Support channel and escalation matrix
- [ ] Pricing and plans configured; trial limits enforced
- [ ] Incident on‑call rota
- [ ] Backup restore drill completed
- [ ] Eval reports archived for the release prompt/model versions
- [ ] Feature flags verified: frozen modules off in production

## 8. MVP risks (top 5; full register in RISKS_AND_DEPENDENCIES)

| Risk | Mitigation |
|---|---|
| Thin listing supply without scraping → poor search results | Sign launch agencies before beta; CSV/API import; limit the launch to cities with supply |
| AI hallucination of property facts → trust damage | Grounding checker, tools‑first prompting, eval gate, quick human escalation |
| Agencies don't respond fast → consumer drop‑off | Response‑time SLAs in plans, AI first response, routing to available agents |
| Scope creep from existing frozen modules | Flags off, a written "not now" list, PM ownership |
| Cost overrun from LLM usage | Real metering, quotas, model tiering, deterministic routes |
