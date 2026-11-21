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

## M1 — Core foundation (Phase 1) — in progress ([MILESTONE_01](milestones/MILESTONE_01_CORE_FOUNDATION.md))

| ID | Status | P | Task | Notes |
|---|---|---|---|---|
| T‑1010 | [x] | P1 | OTP hashing (KI‑016) | ADR‑011. `OTPCode.code_hash` (keyed HMAC bound to phone+purpose), constant‑time check, `users 0013` hashes existing rows; admin no longer lists codes. 9 tests |
| T‑1020 | [x] | P1 | Inbound WhatsApp retry/dead‑letter under DB starvation (KI‑019) | ADR‑012. Retries with backoff on transient DB errors only when nothing was sent; `acks_late`; Redis dead‑letter + `replay_whatsapp_dead_letters`; webhook releases idempotency key on enqueue failure. 16 tests. Outbound outbox split to T‑1021 |
| T‑1021 | [ ] | P1 | Transactional outbox for outbound side effects (WhatsApp sends/notifications written in the DB transaction, dispatched by a relay) | GAP‑043, TR‑09. **Next** |
| T‑1030 | [x] | P1 | Membership model consolidation plan (KI‑010) | ADR‑013 phase A: inventory, canonical `OrganizationMembership`, projection sync hooks, `organizations 0024` backfill, `reconcile_memberships`, deterministic primary org. Fixed stale access after team removal. Also KI‑032 (2 masking exemptions). 25 tests |
| T‑1031 | [ ] | P1 | Policy‑based RBAC + active org context + permission‑matrix tests | ADR‑013 phase B: move `owned_organization`/`agent_profile.organization` readers to memberships; restrict owner/org_admin grants in invite API; KI‑030 |
| T‑1032 | [ ] | P2 | Membership contract (ADR‑013 phase C) | after T‑1031 and a clean `reconcile_memberships` in prod: drop `AgentOrganizationMembership`, re‑key `AgentOrgMembership`, retire `admin_user` one‑to‑one |
| T‑1033 | [ ] | P1 | PII‑masking middleware endpoint audit (KI‑032) | Decide per endpoint which JSON writes legitimately carry PII (lead/agent create, profile PATCH…); owner sign‑off on the policy |
| T‑1040 | [ ] | P2 | `is_sandbox` for unscoped leads (KI‑017) | after T‑1030 |
| T‑1011 | [ ] | P1 | MFA (TOTP) for staff and org admins | GAP‑082 |
| T‑1050 | [ ] | P1 | `Market` entity; remove hardcoded PK defaults; UTC + per‑entity TZ | TR‑04 |
| T‑1060 | [!] | P1 | Location hierarchy + PostGIS + geocoding backfill | Blocked: owner picks geocoding vendor; PostGIS in all envs |
| T‑1070 | [ ] | P1 | Listing schema v2 (expand/contract) | after T‑1050 |
| T‑1080 | [ ] | P2 | Listing lifecycle + transitions + expiry | after T‑1070 |
| T‑1090 | [ ] | P2 | API standards: OpenAPI, error envelope, filter validation, idempotency keys | |
| T‑1100 | [ ] | P2 | Search indexes (composite, GIN, trigram) | |
| T‑1110 | [ ] | P2 | Media pipeline hardening for images | |
| T‑1120 | [ ] | P2 | AI provider abstraction, prompt registry, market‑pack prompts | |

## M2 — AI agent MVP — planned entries referenced by ADRs

| ID | Status | P | Task |
|---|---|---|---|
| T‑2010 | [ ] | P1 | Explicit tool loop for Gemini (accurate per‑round usage, tool auditing) |
| T‑2012 | [ ] | P1 | Durable `ActionConfirmation` record (replace cache‑only proposals) |
| T‑2014 | [ ] | P2 | Listing draft/moderation state (KI‑025) |
| T‑2016 | [ ] | P2 | Context‑free FAQ path so the semantic cache can be enabled safely |
