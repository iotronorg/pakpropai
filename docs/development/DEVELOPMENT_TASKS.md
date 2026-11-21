# Development Tasks

Status: `[ ]` not started · `[~]` in progress · `[x]` completed (tests executed & passed) · `[!]` blocked · `[?]` needs review / decision.
Priority: P0 (blocker) → P3.

## M0 — Stabilisation (Phase 0)

| ID | Status | P | Task | Notes |
|---|---|---|---|---|
| T‑0001 | [x] | P0 | Product assessment and PRD set | `docs/product/ai-real-estate-agent/` (session 1) |
| T‑0002 | [x] | P0 | Reproducible test environment + baseline | `scripts/dev/test.sh`; baseline 1361/1/1; root `conftest.py` cache isolation; export test fixed |
| T‑0003 | [x] | P0 | CI workflow fix | All jobs: repo‑root paths, `development` branch, Bandit HIGH cleared, out‑of‑repo frontend build removed, `infra/scripts/health_check.py` added (tested locally). Validated as YAML + locally; **not yet run on GitHub Actions** |
| T‑0004 | [x] | P0 | Sandbox integrity + consumer visibility (ADR‑002) | Model derivation, 3 backfill migrations, `visible_to()`, search/tool/recs/client API |
| T‑0005 | [x] | P0 | Semantic cache leakage (ADR‑003) | Opt‑in flag, `general_query` only, no digits, org+lang scope |
| T‑0006 | [x] | P0 | Real token accounting (ADR‑004) | `apps/ai/usage.py`; Gemini/Ollama/GeminiClient; AFC residual (KI‑006) |
| T‑0007 | [x] | P0 | Side‑effect tool confirmation (`list_property`, `connect_to_agent`) + deal‑lock freeze (ADR‑005/006) | Audit tool split out to T‑0017. Also fixed `list_property` constraint bug (KI‑021) |
| T‑0008 | [x] | P0 | Scrapers off by default | Messaging updated; code kept |
| T‑0009 | [x] | P1 | Wire `ComplianceEngine` into AI output path | Checked after output guard; single in‑focus DB listing registered by the search tool; 4 tests |
| T‑0010 | [x] | P0 | Field‑level encryption of secrets/PII (ADR‑008) | Approved 2026‑11‑21. 7 migrations, blind index for verify token, encrypted config cache, fail‑closed prod check; forward/reverse rehearsed on Postgres |
| T‑0011 | [x] | P1 | Committed‑artifact removal | Approved. 7 files removed from working tree + `.gitignore` rules. Still in git history (no commits/rewrite per owner) |
| T‑0012 | [x] | P2 | Compare/market‑trends shadowing + param validation | |
| T‑0013 | [x] | P1 | Audit "not a valuation" disclaimer + provenance | Page‑1 notice + corrected final disclaimer in PDF; WhatsApp summary relabelled; `provenance` block in audit data |
| T‑0014 | [x] | P3 | README drift | Cloudinary, no `render.yaml`, encryption keys, test command. Branding (PakProp vs RealTron) unchanged — product decision |
| T‑0015 | [x] | P1 | Freeze flags for frozen modules | Deal lock, `feature_voice_calls` (outbound blocked, inbound hang‑up), `feature_syndication` (create/syndicate/browse blocked). Inventory adapters: per‑connection, unchanged |
| T‑0017 | [x] | P1 | Confirmation guard for `generate_property_audit` | LLM path guarded; direct route = explicit request. Also fixed `PropertyAudit` field‑name bug (KI‑026) |
| T‑0016 | [x] | P1 | Operator go‑live checklist | `GO_LIVE_CHECKLIST.md` |

## M1 — Core foundation (Phase 1) — not started

| ID | Status | P | Task |
|---|---|---|---|
| T‑1010 | [ ] | P1 | OTP hashing (KI‑016) |
| T‑1020 | [ ] | P1 | Transactional outbox / retry for WhatsApp under DB starvation (KI‑019) |
| T‑1030 | [ ] | P1 | Membership model consolidation plan (KI‑010) |
| T‑1040 | [ ] | P2 | `is_sandbox` for unscoped leads (KI‑017) |

## M2 — AI agent MVP — planned entries referenced by ADRs

| ID | Status | P | Task |
|---|---|---|---|
| T‑2010 | [ ] | P1 | Explicit tool loop for Gemini (accurate per‑round usage, tool auditing) |
| T‑2012 | [ ] | P1 | Durable `ActionConfirmation` record (replace cache‑only proposals) |
| T‑2014 | [ ] | P2 | Listing draft/moderation state (KI‑025) |
| T‑2016 | [ ] | P2 | Context‑free FAQ path so the semantic cache can be enabled safely |
