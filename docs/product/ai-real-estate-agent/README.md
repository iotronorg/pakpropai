# AI Real Estate Agent — Product Documentation

Assessment and PRD set for the PakProp AI / RealTron AI backend (`pakpropai` @ `0b574a7`, 2026-11-21).

| # | Document | Purpose |
|---|---|---|
| 1 | [EXISTING_SYSTEM_ASSESSMENT.md](EXISTING_SYSTEM_ASSESSMENT.md) | What the backend actually does today: architecture, feature/API/data inventory, AI, security, debt, maturity |
| 2 | [PRD_MASTER.md](PRD_MASTER.md) | Master PRD, Parts A–I: strategy, personas, module requirements, technical/global/AI/ops/business requirements, delivery |
| 3 | [FEATURE_GAP_ANALYSIS.md](FEATURE_GAP_ANALYSIS.md) | Existing vs required state, gap IDs, priority register, retain/redesign/freeze decisions |
| 4 | [TECHNICAL_RECOMMENDATIONS.md](TECHNICAL_RECOMMENDATIONS.md) | Evidence‑based architecture, DB, RBAC, API, search, AI runtime, security, CI/CD recommendations |
| 5 | [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md) | Phases 0–5 with deliverables, dependencies, risks, completion criteria, durations |
| 6 | [MVP_SCOPE.md](MVP_SCOPE.md) | Included / deferred / frozen, acceptance and launch readiness criteria |
| 7 | [RISKS_AND_DEPENDENCIES.md](RISKS_AND_DEPENDENCIES.md) | Scored risk register, dependencies, assumptions |
| 8 | [API_AND_DATA_REQUIREMENTS.md](API_AND_DATA_REQUIREMENTS.md) | Future API map, entities, ERD, DB requirements, integration catalogue |
| 9 | [AI_CAPABILITIES_SPECIFICATION.md](AI_CAPABILITIES_SPECIFICATION.md) | Agent runtime, tools, schemas, memory, RAG, safety, evaluation, observability, cost |
| 10 | [PRD_CHANGELOG.md](PRD_CHANGELOG.md) | Versions, decisions, limitations, open questions |

**Reading order:** 1 → 3 → 2 → 6 → 5 → the specialist docs (4, 8, 9) → 7.

**ID conventions:** `GAP-NNN` gaps · `FR-<MOD>-NNN` functional requirements · `NFR-*` non‑functional · `TR-NN` technical recommendations · `AIC-NN` AI capabilities · `R-NN` risks · `D-NN` dependencies · `A-NN` assumptions · `DEC-NN` decisions · `TD-NN` technical debt.
