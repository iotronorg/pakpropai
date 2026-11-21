# PRD Changelog

| Version | Date | Author | Summary |
|---|---|---|---|
| 1.0 | 2026-11-21 | Product architecture review (AI‑assisted) | Initial assessment and PRD set created from inspection of `pakpropai` @ `0b574a7` (branch `development`). |

## v1.0 — key decisions

| # | Decision | Rationale | Document |
|---|---|---|---|
| DEC‑01 | **Evolve the Django modular monolith. No rewrite, no microservices.** | The stack fits the product and team size. The problems are in the domain model and AI orchestration, not the framework. | TECHNICAL_RECOMMENDATIONS §1 |
| DEC‑02 | **Remove portal scraping from the production path** (Zameen/Graana) pending legal review | Terms and licensing exposure; supply must be first‑party or licensed | FEATURE_GAP_ANALYSIS GAP‑070, RISKS R‑01 |
| DEC‑03 | **Freeze, don't delete,** non‑core modules: Deal Lock/escrow, voice, syndication/commission ledger, regional clusters, fine‑tuning curation, unagreed inventory adapters | Regulatory complexity and focus; the code stays available for later phases | MVP_SCOPE §4.2 |
| DEC‑04 | **Primary validation market: Pakistan; UAE as configuration‑ready second market** | Depth of the existing implementation; global foundations through the `Market` entity | PRD_MASTER §B.8, MVP_SCOPE §2 |
| DEC‑05 | **Single agent runtime with modes and a platform‑controlled tool loop** (replace Gemini Automatic Function Calling) | Tool authorisation, confirmation, logging and grounding cannot be enforced inside an SDK‑managed loop | AI_CAPABILITIES_SPECIFICATION §3 |
| DEC‑06 | **Not multi‑agent** for now | No clear advantage; adds latency, cost and complexity | AI spec §1 |
| DEC‑07 | **Postgres hybrid search (PostGIS + FTS + pgvector) before any dedicated search engine** | Sufficient at MVP scale; one datastore; explicit triggers to revisit | TECHNICAL_RECOMMENDATIONS §5, §8 |
| DEC‑08 | **Deterministic, explainable match scoring**; the LLM explains but does not rank. `ai_score` (LLM opinion) is replaced by a quality score in ranking | Reproducibility, fairness, trust | PRD C.4, GAP‑076 |
| DEC‑09 | **Paid placement is labelled and never alters AI match scores** | Consumer trust | PRD Part H |
| DEC‑10 | **Policy‑based RBAC with scoped role assignments** replacing three membership tables and hardcoded roles | Multi‑org users, market admins, consistent enforcement | TECHNICAL_RECOMMENDATIONS §6 |
| DEC‑11 | **No valuation claims.** Property audits are reframed as "indicative price context" with provenance and disclaimers | Liability; data limitations | PRD C.6 |
| DEC‑12 | **Web chat API added alongside WhatsApp** | WhatsApp‑only limits reach and SEO; one runtime for both | PRD C.1 FR‑AI‑001 |

## Known limitations of v1.0

- The test suite was **not executed** (Django is not installed locally and installing packages was out of scope). Maturity scores assume the code behaves as written.
- The frontend repository `../pakpropaiweb` was **not assessed**.
- No market sizing, competitor analysis or pricing research was performed. These are listed as research tasks.
- Duration estimates are assumption‑based ranges (see PRODUCT_ROADMAP, "Estimation assumptions").
- Third‑party API availability (e.g. Rightmove, Bayut, Zameen partner feeds; IDV contracts) was not verified.

## Open questions for v1.1

1. Final brand: PakProp AI or RealTron AI?
2. Legal position on scraping history and existing scraped data in caches/DB (if any persisted).
3. Do private (non‑agent) sellers list directly in the launch market at MVP, or only professionals?
4. Which secondary LLM provider, based on the Phase 2 eval comparison?
5. Launch cities and listing‑supply commitments from agencies.
6. Data retention periods per data class for the launch market (counsel).
