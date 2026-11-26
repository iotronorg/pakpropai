# M3 — Professional real estate workspace (PRD Phase 3)

**Goal:** make agencies successful and willing to pay. Sources: [PRODUCT_ROADMAP § Phase 3](../../product/ai-real-estate-agent/PRODUCT_ROADMAP.md), [PRD_MASTER](../../product/ai-real-estate-agent/PRD_MASTER.md) FR‑CRM‑001…009, FR‑LAI‑001…003, FR‑LST‑012, [FEATURE_GAP_ANALYSIS](../../product/ai-real-estate-agent/FEATURE_GAP_ANALYSIS.md) GAP‑016/100/103/104.

**Started:** 2026‑11‑26 (session 32). M2 is code complete apart from owner work (T‑2072, T‑2080); M3 tasks that need no owner input go first.

## Exit criteria (PRD Phase 3 completion)
| # | Criterion | State |
|---|---|---|
| 1 | MVP acceptance criteria 10–15 met in production | Built in M2; live once deployed (owner) |
| 2 | First paid conversions | Needs plans as data (T‑3080) and pricing (owner) |
| 3 | Agencies use the workspace weekly (metric defined in Phase 2) | Measurable from T‑3010 onwards; the weekly-use definition is the owner's |

## Tasks
| ID | P | Deliverable | Ref | Depends on |
|---|---|---|---|---|
| T‑3010 ✅ | P1 | Agent performance from real events: enquiry first-reply time, viewings held / missed, visitor feedback, lead conversion, per period in the organization's time | FR‑CRM‑008 | — |
| T‑3020 ✅ | P1 | Tasks and follow-ups with due dates and reminders (outbox), on leads | FR‑CRM‑004 | — |
| T‑3030 | P1 | Configurable pipeline stages per organization (default new → contacted → qualified → viewing → offer → won/lost), expand/contract over the fixed `Lead.Status`; `routing_state` kept | FR‑CRM‑002 | frontend contract |
| T‑3040 | P1 | Team management and assignment rules (round-robin, area, language, load) extending auto-assign | FR‑CRM‑007 | T‑3030 |
| T‑3050 ✅ | P1 | AI listing assistant: title/description only from structured fields and seller notes (unsupported sentences marked), missing-information check against the market schema, attribute suggestions from free text that need confirmation | FR‑LAI‑001…003 | eval cases (T‑2070 harness) |
| T‑3060 ✅ | P1 | Listing analytics: impressions, detail views, saves, enquiries, conversion (aggregate counts, no personal tracking) | FR‑LST‑012 | — |
| T‑3070 | P1 | Unified audit log: one trail for the three overlapping logs, with a tamper-evident chain | GAP‑104 | — |
| T‑3080 | P1 | Plans and entitlements as data; limits enforced from them | GAP‑100 | owner: pricing |
| T‑3090 | P1 | AI ops console APIs: prompts, models and prices, eval runs, traces, cost, kill switch | GAP‑103 | — |
| T‑3100 | P1 | Unified contact (person ↔ leads) and unified messages (`ConversationMessage` + `WhatsAppMessage`) | FR‑CRM‑001, GAP‑016 | T‑3030; migration plan |
| T‑3110 | — | Public launch in the primary market (consumer beta → GA) | Release | owner |

## Order
T‑3010 → T‑3020 → T‑3060 → T‑3050 → T‑3070 → T‑3090 → T‑3030 → T‑3040 → T‑3100 → T‑3080 → T‑3110. Tasks that change the frontend contract (T‑3030, T‑3100) come after the additive ones.

## Owner dependencies
- Pricing research for plans (T‑3080).
- The weekly-use metric definition (exit criterion 3).
- Frontend work for pipelines, tasks and analytics.
