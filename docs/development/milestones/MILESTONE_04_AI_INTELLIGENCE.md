# M4 — AI intelligence (PRD Phase 4)

**Goal:** grounded AI help beyond search: knowledge with citations, comparison narratives, price context, investment figures, documents. Sources: [PRODUCT_ROADMAP § Phase 4](../../product/ai-real-estate-agent/PRODUCT_ROADMAP.md), [AI_CAPABILITIES_SPECIFICATION §7, §12](../../product/ai-real-estate-agent/AI_CAPABILITIES_SPECIFICATION.md), [PRD_MASTER](../../product/ai-real-estate-agent/PRD_MASTER.md) FR‑INV, FR‑CMP‑003/004, FR‑VAL‑001…004.

**Started:** 2026‑11‑26 (session 34). M3 is done apart from owner work (T‑3080 pricing, T‑3110 launch) and the release-gated T‑3100c. M4 tasks that need no licensed data or vendor go first.

## Exit criteria (PRD Phase 4 completion)
| # | Criterion | State |
|---|---|---|
| 1 | Each feature passes its eval/grounding suite | Per task: deterministic grounding checks in tests; model eval cases with T‑2070 harness |
| 2 | Disclaimers have legal sign-off | Owner (legal); AI commentary flags ship **off** until then |

## Tasks
| ID | P | Deliverable | Ref | Depends on |
|---|---|---|---|---|
| T‑4010 ✅ | P2 | Investment calculator (deterministic, formulas and sourced assumptions) + labelled AI commentary | FR‑INV‑001…004 | — |
| T‑4020 ✅ | P2 | Indicative price context: price-per-m² distribution of comparable listings, N, dispersion, age, provenance, hidden below a threshold (audit redesign) | FR‑VAL‑001…004 | — |
| T‑4030 ✅ | P2 | Comparison narratives constrained to the comparison table; running-cost estimates from market calculators | FR‑CMP‑003/004 | T‑4010 (costs) |
| T‑4040 | P2 | RAG knowledge (market + organization), `KnowledgeChunk`, hybrid retrieval, citations | AI spec §7 | embedding model (pgvector present) |
| T‑4050 ✅ | P2 | Persistent duplicate clustering; fraud signals v2 | FR‑LST‑010 | — |
| T‑4060 ✅ | P2 | Offers register on leads | FR‑CRM‑009 | — |
| T‑4070 | P2 | Document intelligence with a real OCR backend + human review | AIC‑14 | owner: OCR vendor |
| T‑4080 | P2 | POI proximity search | FR‑SRCH‑003 | owner: licensed POI data |
| T‑4090 | P3 | Learned re-ranking experiments (A/B) | FR‑REC‑006 | interaction volume |
| T‑4100 | P2 | Push notifications; calendar sync | FR‑COM‑004, FR‑APT‑007 | owner: FCM/APNs, Google/Microsoft apps |

## Order
T‑4010 → T‑4020 → T‑4030 → T‑4060 → T‑4050 → T‑4040 → (owner-gated) T‑4070, T‑4080, T‑4100 → T‑4090.

## Owner dependencies
- Legal sign-off on the investment and price-context disclaimers (then turn on `feature_investment_commentary`).
- OCR vendor, POI data licence, push and calendar provider accounts.
