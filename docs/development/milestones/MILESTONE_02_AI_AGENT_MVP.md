# M2 — AI Real Estate Agent MVP (PRD Phase 2)

**Goal:** the consumer value loop on web and WhatsApp: discover → shortlist → enquire → book a viewing. Sources: [PRODUCT_ROADMAP § Phase 2](../../product/ai-real-estate-agent/PRODUCT_ROADMAP.md), [AI_CAPABILITIES_SPECIFICATION](../../product/ai-real-estate-agent/AI_CAPABILITIES_SPECIFICATION.md) §3–§11, [MVP_SCOPE §6](../../product/ai-real-estate-agent/MVP_SCOPE.md).

**Started:** 2026‑11‑23 (session 9). M1's code work is complete; its two contract migrations (T‑1070g‑2, T‑1032b‑2) ship with later releases.

## Exit criteria (MVP acceptance criteria 1–9)
| # | Criterion | State |
|---|---|---|
| 1 | Discover → shortlist → enquire → book on web and WhatsApp with no staff intervention (staging E2E) | Passes locally against real server, workers, Postgres and Redis with a stand-in model and WhatsApp API (`scripts/dev/e2e.sh`, session 23). The staging run with the release model is the owner's |
| 2 | The same `SearchCriteria` gives identical results via the search API and the AI tool | Checked in the E2E journey for one query, web assistant and WhatsApp |
| 3 | Every listing shown is published, in a live market, not sandbox, first‑party/licensed | Met for the consumer surfaces found in review (the disabled deal tools excluded, `under_offer` shown as before): tested against expired, inactive-market, sandbox, draft, suspended and sold listings (session 25, ADR‑048). Scrapers are off by default |
| 4 | Agents receive enquiries within 1 minute and can reply | Measured per channel (session 27, ADR‑050): `realtron_notification_delivery_seconds`, the backlog alert and `manage.py notification_delivery_report`; reply checked in the E2E journey. WhatsApp delivery to the agent needs the approved template; production figures need the release running |
| 5 | Saved‑search alerts arrive within the configured frequency | Met: worst case is the frequency plus one 15-minute run, proven with simulated time; go-live listings included (session 25). Email and WhatsApp delivery in staging pending |
| 6 | Criteria‑extraction F1 meets the Phase 2 baseline per launch language | Harness and 18 seed cases per language built (T‑2070); no live run or baseline yet (T‑2072) |
| 7 | Zero critical hallucinations in the release eval suite; grounding checker live | Checker live (T‑2013); deterministic suite passes in CI form (T‑2070); a run with the release model is T‑2072 |
| 8 | Side‑effect tools never execute without a confirmation record (tests) | Met for the existing tools (T‑2012); a test requires every committing registry tool to be confirmable. Enquiry, booking and saved‑search tools come later |
| 9 | AI cost per conversation and per qualified lead from real token data | Built (T‑2011, `GET /ai/costs/`); needs operator‑entered `ModelPrice` rows |
| 10 | No plaintext secrets or national IDs in the database | Met in code: schema test and scan on test data (session 26); run `scan_plaintext` on production data |
| 11 | Export and deletion cover conversations, memory, enquiries, messages, within the SLA | Built and tested (session 26); session 28: export open to everyone, 30-day SLA confirmed, Cloudinary files deleted; open: self-service deletion outside GDPR markets (KI‑112) |
| 14 | SLO dashboards and alerts live for API, search, AI reply and webhooks | Built and tested (session 27, ADR‑050): objectives, burn-rate alerts (promtool tests in CI), SLO dashboard; worker metrics now exported. Session 28: self-hosted stack with email alerts, tested end to end (scrape with token, alert email received). **Live** once deployed (checklist §52) |
| 15 | Runbooks for LLM outage, WhatsApp token expiry, data incidents, harmful AI output | Met (session 29, ADR‑052): `docs/runbooks/`, linked from every alert; new alerts for each situation; checked by `tests/test_runbooks.py`. Roles to be named by the owner (checklist §53) |

## Tasks
| ID | P | Deliverable | Ref | Depends on |
|---|---|---|---|---|
| T‑2010 ✅ | P1 | Explicit tool loop: the provider returns tool calls and the runtime executes them; tool registry with per‑tool policy; per‑step usage | AI spec §3.1/§3.2/§5 | T‑1120 |
| T‑2011 ✅ | P1 | `AITrace` (per turn: prompt version, provider, tokens, cost, latency, tool calls, guard/grounding results, fallback) + `ModelPrice` cost | AI spec §10/§11 | T‑2010 |
| T‑2012 ✅ | P1 | Durable `PendingAction` confirmations (replace the cache‑only proposals) | AI spec §5, MVP 8 | T‑2010 |
| T‑2013 ✅ | P1 | Grounding checker v2 (claims vs cited sources; one regeneration then a safe fallback) | AI spec §3.1/§8.2, MVP 7 | T‑2010 |
| T‑2014 ✅ | P2 | Listing draft/moderation state; `draft_listing` tool draft‑only; moderation queue and user reports | KI‑025, G2 | T‑1080, T‑2012 |
| T‑2016 ✅ | P2 | Context‑free FAQ path so the semantic cache can be enabled safely | ADR‑003 | T‑2010 |
| T‑2020 ✅ | P1 | Durable `Conversation`/`Message` (Postgres, Redis window cache); conversation API (SSE); WhatsApp adapter on the runtime | FR‑AI‑001/002/004 | T‑2010 |
| T‑2030 ✅ | P1 | `SearchCriteria` extraction and clarification; SearchService v2 (filters + geo + text) and ranking with explanations; `search_listings`/`get_listing` tools | FR‑AI‑003, FR‑SRCH‑*, FR‑REC‑001/003, MVP 2 | T‑2010 |
| T‑2031 ✅ | P1 | Public listing read API | GAP‑002 | T‑2030 |
| T‑2040 ✅ | P2 | Preference profile with consent; favourites (`save_listing`); saved searches and alerts (email, WhatsApp) | FR‑AI‑008, FR‑REC‑002/004, MVP 5 | T‑2030, T‑2012 |
| T‑2041 ✅ | P2 | Consumer comparison (`compare_listings`) | FR‑CMP‑001/002 | T‑2030 |
| T‑2050 ✅ | P1 | Enquiries → leads (`create_enquiry` with consent); in‑app threads; email notifications | FR‑COM‑001–003, MVP 4 | T‑2012 ✅ |
| T‑2060 ✅ | P1 | Availability, slot booking (`find_viewing_slots`/`book_viewing`), reminders, feedback | FR‑APT‑001–006 | T‑2012 ✅ |
| T‑2070 ✅ | P1 | Eval suites (EN/UR/Roman Urdu) with a CI gate; kill switch per market/org (provider failover done in T‑1120) | AI spec §9, MVP 6/7 | T‑2010, T‑2030 |
| T‑2071 ✅ | P1 | Indirect prompt injection (lister text as delimited data; eval suite) | AI spec §8.1 | T‑2070 |
| T‑2051 ✅ | P2 | Thread photos, block, report, warnings | FR-COM-001/007 | T‑2050 |
| T‑2072 | P1 | Reviewed golden set (≥300 per language); live run and baseline | AI spec §9, MVP 6 | owner |
| T‑2080 | — | Closed beta with launch agencies | Release | owner |

## Order
T‑2010 → T‑2012 → T‑2011 → T‑2013 → T‑2020 → T‑2030 → T‑2031 → T‑2050 → T‑2060 → T‑2040 → T‑2041 → T‑2014 → T‑2016 → T‑2070 → T‑2071 → T‑2051 → T‑2072 → T‑2080.

## Owner dependencies (from the PRD)
- Frontend chat, search and listing UI.
- A secondary LLM contract: failover is built but off (T‑1120, GO_LIVE §28).
- An email provider (T‑2040/T‑2050).
- Golden datasets per language (T‑2070).
- Launch agencies' listings (beta).
