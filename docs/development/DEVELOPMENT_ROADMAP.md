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
| [M6 Production readiness](milestones/MILESTONE_06_PRODUCTION_READINESS.md) | cross‑phase | **In progress** (2/5: T‑6010 backups and restore drill, T‑6040 release check) | Proven restore, load vs NFRs, security review, release flag audit |

The M5 milestone file will be created when it starts. Their scope is defined in the PRD roadmap.

## M6 — Production readiness ([MILESTONE_06](milestones/MILESTONE_06_PRODUCTION_READINESS.md))
| ID | Status | P | Task | Notes |
|---|---|---|---|---|
| T‑6010 | [x] | P1 | Backups and restore drill | ADR‑071; owner: off-host storage, install timers, first drill (§71) |
| T‑6020 | [ ] | P1 | Load test vs NFR targets | |
| T‑6030 | [ ] | P1 | Security review pass | |
| T‑6040 | [x] | P2 | Release check command | ADR‑072; deploy runs it; owner: set `ENVIRONMENT`, run it once per server (§72) |
| T‑6050 | [ ] | P2 | Capacity and recovery notes | Owner: RPO/RTO |
