# Risks & Dependencies

| | |
|---|---|
| **Version** | 1.0 · 2026-11-21 |
| **Related** | [PRODUCT_ROADMAP](PRODUCT_ROADMAP.md) · [MVP_SCOPE](MVP_SCOPE.md) · [FEATURE_GAP_ANALYSIS §2](FEATURE_GAP_ANALYSIS.md#2-classification-summaries) |

Scoring: **Likelihood (L)** and **Impact (I)** on 1–5; **Exposure = L × I**. Owners are roles. Assign names during planning.

## 1. Risk register

### 1.1 Legal, regulatory and data

| ID | Risk | L | I | Exp | Mitigation | Owner |
|---|---|:-:|:-:|:-:|---|---|
| R‑01 | **Scraping Zameen/Graana** breaches terms or IP rights, leading to legal action, blocking or reputational harm | 4 | 5 | 20 | Disable now (Phase 0); legal review; replace with first‑party and licensed feeds | PM + Legal |
| R‑02 | **Deal Lock** (platform‑mediated token payments) is treated as regulated escrow/payment services | 3 | 5 | 15 | Freeze; obtain a legal opinion; if revived, use a licensed escrow partner | Legal |
| R‑03 | AI outputs read as **valuations, financial, tax or legal advice** | 3 | 4 | 12 | Disclaimers, provenance labels, calculators separated from commentary, policy evals | PM + Legal |
| R‑04 | **Privacy non‑compliance** in launch market (consent, retention, cross‑border LLM processing) | 3 | 5 | 15 | Legal review per market; data map; DPAs with vendors; residency options | Compliance |
| R‑05 | **Discrimination/steering** by AI (e.g. "family‑friendly", "safe" areas) | 3 | 4 | 12 | Fairness policy, steering eval suite, neutral objective data only | AI lead + Legal |
| R‑06 | **Listing advertising rules** (permit/licence numbers required in some markets) | 3 | 3 | 9 | Market schema enforces required fields | PM |
| R‑07 | Data licensing costs for POI/market data are higher than expected | 3 | 3 | 9 | Use open data first; defer to Phase 4; get quotes early | PM |
| R‑08 | WhatsApp policy changes or account restrictions (templates, marketing opt‑in, pricing) | 3 | 4 | 12 | Opt‑in enforcement; template compliance; web chat as a second channel | Eng lead |
| R‑09 | Voice call recording consent laws | 2 | 3 | 6 | Voice frozen; consent flows before revival | Legal |

### 1.2 Security & privacy

| ID | Risk | L | I | Exp | Mitigation | Owner |
|---|---|:-:|:-:|:-:|---|---|
| R‑10 | **Plaintext secrets** in DB exposed via a backup, a DB leak or admin misuse | 3 | 5 | 15 | Field encryption + KMS; rotate credentials; restrict backup access | Security |
| R‑11 | **Semantic cache cross‑user leakage** within an org | 3 | 4 | 12 | Restrict to public intents; key by prompt version + market + language | AI lead |
| R‑12 | **Prompt injection** (direct, or indirect via listing descriptions/documents) triggers tools | 3 | 4 | 12 | Explicit tool loop, policies, confirmations, data delimiting, red‑team evals | AI lead |
| R‑13 | Committed artifacts contain PII (audit PDFs, vector DB) | 2 | 4 | 8 | Review, remove, scrub history if needed | Eng lead |
| R‑14 | Account takeover of org admins (no MFA) | 2 | 4 | 8 | MFA, session management, anomaly alerts | Security |
| R‑15 | Malicious uploads | 2 | 3 | 6 | Media pipeline, scanning, private bucket | Eng |
| R‑16 | Listing fraud/scams erode trust | 4 | 4 | 16 | Verification badges, moderation, fraud signals, user reports, takedown SLA | Ops |

### 1.3 AI

| ID | Risk | L | I | Exp | Mitigation | Owner |
|---|---|:-:|:-:|:-:|---|---|
| R‑20 | **Hallucinated property facts** | 4 | 5 | 20 | Grounding checker, tools‑first, eval gate, low temperature | AI lead |
| R‑21 | **Unmeasured AI cost** (token accounting broken) → margin loss | 4 | 4 | 16 | Fix accounting in Phase 0; quotas; tiering | AI lead + Finance |
| R‑22 | Single‑provider outage or model deprecation | 3 | 4 | 12 | Provider abstraction + failover; pin model versions; deprecation monitoring | AI lead |
| R‑23 | Multilingual quality gaps (Urdu/Roman Urdu/Arabic) | 3 | 4 | 12 | Per‑language eval sets; native reviewers | AI lead |
| R‑24 | Latency too high on WhatsApp/web | 3 | 3 | 9 | Streaming, small models for extraction, caching public content | Eng |
| R‑25 | Regex guardrails over‑block legitimate users | 3 | 2 | 6 | Classifier layer, false‑positive monitoring, appeal to human | AI lead |

### 1.4 Technical & delivery

| ID | Risk | L | I | Exp | Mitigation | Owner |
|---|---|:-:|:-:|:-:|---|---|
| R‑30 | **Unknown test health** (suite not executed in this assessment) → hidden breakage | 3 | 4 | 12 | Phase 0 baseline first | Eng lead |
| R‑31 | **CI paths broken** → no safe deploys | 4 | 3 | 12 | Fix in week 1 | DevOps |
| R‑32 | Listing schema migration corrupts or loses data | 2 | 5 | 10 | Expand/contract, dry runs, backups, reconciliation reports | Eng lead |
| R‑33 | RBAC consolidation causes privilege bugs or cross‑tenant leaks | 3 | 5 | 15 | Permission matrix tests; existing cross‑org isolation tests extended; staged rollout | Eng lead |
| R‑34 | Scope creep from the many existing modules | 4 | 3 | 12 | Freeze list; PM gatekeeping | PM |
| R‑35 | Team capacity is below the roadmap assumption | 3 | 3 | 9 | Re‑plan ranges (×1.5–2); cut P2 scope | PM |
| R‑36 | Frontend (separate repo, not assessed) lags the backend | 3 | 4 | 12 | Joint planning; API‑first with OpenAPI mocks | PM |
| R‑37 | Chaos findings (no buffering/retry under DB starvation) cause message loss at peak | 2 | 4 | 8 | Outbox + retries; re‑run chaos tests | Eng |
| R‑38 | Celery/DB log growth degrades performance | 3 | 2 | 6 | Retention, partitioning | Eng |

### 1.5 Business & operational

| ID | Risk | L | I | Exp | Mitigation | Owner |
|---|---|:-:|:-:|:-:|---|---|
| R‑40 | **Insufficient listing supply** after scraping is removed | 4 | 5 | 20 | Sign agencies before beta; easy import; focus on a few cities | Sales/PM |
| R‑41 | Agencies won't pay or churn | 3 | 5 | 15 | Customer discovery; value metrics (response time, qualified leads); pilot pricing | PM |
| R‑42 | Slow agent responses ruin the consumer experience | 3 | 4 | 12 | Response SLAs, routing, AI first response, visibility in ranking for responsive agents (disclosed) | Ops |
| R‑43 | Monetisation that undermines trust (pay‑to‑rank in AI) | 2 | 5 | 10 | Policy: sponsored is labelled and separate from the AI score | PM |
| R‑44 | Moderation workload exceeds staff | 3 | 3 | 9 | Automated pre‑checks; prioritised queues | Ops |
| R‑45 | Brand confusion (PakProp vs RealTron) | 3 | 2 | 6 | Branding decision in Phase 0 | Leadership |

### 1.6 Top exposures (≥15)

R‑01 scraping (20) · R‑20 hallucination (20) · R‑40 supply (20) · R‑16 fraud (16) · R‑21 AI cost (16) · R‑02 deal lock (15) · R‑04 privacy (15) · R‑10 secrets (15) · R‑33 RBAC (15) · R‑41 willingness to pay (15).

## 2. Dependencies

| ID | Dependency | Type | Needed for | Lead time (`ASSUMPTION`) | Fallback |
|---|---|---|---|---|---|
| D‑01 | Legal review: launch market (privacy, advertising, AI disclosure, payments) | Legal | Public beta | 4–8 weeks | Delay public beta; closed beta under NDA |
| D‑02 | Legal opinion: scraping, deal lock | Legal | Phase 0 decisions | 2–4 weeks | Keep disabled |
| D‑03 | Launch agency partners with listings | Business | Phase 2 | Ongoing | Narrow city scope |
| D‑04 | Secondary LLM vendor and DPA | Vendor | Phase 2 | 2–6 weeks | Degraded deterministic mode |
| D‑05 | Maps/geocoding vendor | Vendor | Phase 1 | 1–3 weeks | Open data + manual locations |
| D‑06 | Email provider | Vendor | Phase 2 | 1–2 weeks | WhatsApp only |
| D‑07 | KMS / secret manager | Infra | Phase 0 | 1 week | Env‑based key (interim) |
| D‑08 | Postgres with PostGIS/pgvector (managed) | Infra | Phase 1/4 | 1–2 weeks | Self‑managed |
| D‑09 | Frontend team capacity | Engineering | Phase 2 | — | Simplified web chat widget |
| D‑10 | Golden datasets and native‑language reviewers | Data | Phase 2 | 3–6 weeks | Smaller initial sets |
| D‑11 | WhatsApp Business verification per agency | Vendor | Phase 2 | Days–weeks | Platform number for beta |
| D‑12 | Licensed POI/market data | Vendor | Phase 4 | 4–12 weeks | Open data subsets |
| D‑13 | Pentest vendor | Vendor | Pre‑launch | 2–4 weeks | Internal review (weaker) |

## 3. Assumptions log

| ID | Assumption | Validate by |
|---|---|---|
| A‑01 | Pakistan is the right primary market given code depth | Customer discovery + market research (Phase 0–2) |
| A‑02 | Agencies will pay for AI first response and qualification | Pilot pricing in beta |
| A‑03 | Postgres hybrid search meets latency targets at MVP scale | Load tests in Phase 1–2 |
| A‑04 | Team size as stated in the roadmap | Staffing plan |
| A‑05 | Gemini remains viable as the primary provider for cost/quality in launch languages | Phase 2 eval comparison |
| A‑06 | The existing test suite is largely passing | Phase 0 baseline |
| A‑07 | Consumers in the launch market accept web chat in addition to WhatsApp | Beta analytics |
