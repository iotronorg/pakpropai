# Development Roadmap

Milestones follow the PRD roadmap phases (ADR‑000). Source: [PRODUCT_ROADMAP](../product/ai-real-estate-agent/PRODUCT_ROADMAP.md).

| Milestone | Phase | Status | Exit criteria (summary) |
|---|---|---|---|
| [M0 Stabilisation](milestones/MILESTONE_00_STABILIZATION.md) | Phase 0 | **Code complete** — awaiting first CI run on Actions | CI green; no plaintext secrets; scrapers off; sandbox exclusion tested; real token accounting; frozen modules off |
| [M1 Core foundation](milestones/MILESTONE_01_CORE_FOUNDATION.md) | Phase 1 | **In progress** (16/18 + T‑1032b‑1 + T‑1092; the rest are release‑gated contracts + subtasks T‑1032a, T‑1032c, T‑1070a–f, T‑1070g‑1; T‑1060 done) | Market/location model, listing v2, RBAC consolidation, API standards |
| [M2 AI agent MVP](milestones/MILESTONE_02_AI_AGENT_MVP.md) | Phase 2 | **In progress** (16/17: all but T‑2072) | Explicit tool runtime, AITrace, durable confirmations, enquiries, viewings |
| [M3 Professional workspace](milestones/MILESTONE_03_PROFESSIONAL_WORKSPACE.md) | Phase 3 | **In progress** (8/11 + T‑3100a/b: T‑3010, T‑3020, T‑3060, T‑3050, T‑3070, T‑3090, T‑3030, T‑3040; T‑3100c waits for the production backfill; owner-blocked T‑3080, T‑3110) | Agency workspace, listing assistant, plans |
| [M4 AI intelligence](milestones/MILESTONE_04_AI_INTELLIGENCE.md) | Phase 4 | **In progress** (5/10: T‑4010, T‑4020, T‑4030, T‑4060, T‑4050; + follow-up T‑4051) | RAG, price context, investment, documents |
| M5 Expansion | Phase 5 | Not started | Second market, partners |
| M6 Production readiness | cross‑phase | Not started | Ops, security review, load, DR |

Milestone files for M5–M6 will be created when each milestone starts. Their scope is defined in the PRD roadmap.
