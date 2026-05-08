# PakProp AI — Build Progress

**Last updated:** 2026-05-08 (session 8)  
**Current branch:** `development`  
**Current phase:** Phase 2

---

## Phase 1 MVP Checklist

| Feature | Status | File(s) |
|---------|--------|---------|
| Project scaffold (Django modular monolith) | ✅ Done | `config/`, `apps/` |
| Custom user model (phone-based, RBAC) | ✅ Done | `apps/users/models.py` |
| OTP authentication (issue + verify + JWT) | ✅ Done | `apps/users/services.py`, `views.py`, `urls.py` |
| WhatsApp OTP delivery (async, template-aware) | ✅ Done | `apps/notifications/tasks.py`, `apps/whatsapp/client.py` |
| WhatsApp webhook (signature verify, idempotency) | ✅ Done | `apps/whatsapp/views.py` |
| AI infrastructure (Gemini, Redis cache, circuit breaker) | ✅ Done | `apps/ai/client.py` |
| Prompt library | ✅ Done | `services/prompt_library.py` |
| AI orchestrator | ✅ Done | `services/ai_orchestrator.py` |
| Scam Check flow (WhatsApp) | ✅ Done | `apps/verification/services.py`, `apps/whatsapp/router.py` |
| Multi-step conversation state machine (FSM) | ✅ Replaced by AI agent | `apps/ai/agent.py` |
| Tax (7E) advisory flow | ✅ Done (tool-based) | `apps/ai/tools.py` → `calculate_7e_tax()` |
| Loan eligibility flow | ✅ Done (tool-based) | `apps/ai/tools.py` → `check_loan_eligibility()` |
| Lead capture (auto on every completed flow) | ✅ Done | `apps/ai/tools.py` → `list_property()`, `apps/leads/models.py` |
| Property listing via WhatsApp (AI-guided) | ✅ Done (AI-native) | `apps/ai/tools.py` → `list_property()` |
| Property search — DB listings | ✅ Done | `apps/properties/search.py` |
| Property search — web scrapers (Zameen, Graana, OLX) | ✅ Done | `apps/properties/scrapers/` |
| Modular scraper registry (add sites in one line) | ✅ Done | `apps/properties/scrapers/registry.py` |
| Paginated search results (3 per message, "more" to continue) | ✅ Done | `apps/properties/search.py` |
| AI batch verdicts on search results | ✅ Done | `services/ai_orchestrator.py` → `batch_verdicts()` |
| Voice message transcription (Gemini multimodal) | ✅ Done | `apps/ai/backends/gemini.py`, `apps/whatsapp/router.py` |
| Image / document analysis via WhatsApp | ✅ Done | `apps/ai/agent.py` → `chat_with_image()` |
| Furnished / unfurnished / semi-furnished filter | ✅ Done | `apps/properties/models.py`, AI tool params |
| Builder / ready / under-construction filter | ✅ Done | `apps/properties/models.py`, AI tool params |
| AI scoring task (async, Celery) | ✅ Done | `apps/properties/tasks.py` |
| **AI Agent (reasoning + tools + memory)** | ✅ Done | `apps/ai/agent.py`, `apps/ai/tools.py`, `apps/ai/knowledge.py` |
| **Pakistani RE knowledge base** | ✅ Done | `apps/ai/knowledge.py` |
| **SDK migration (google.generativeai → google.genai)** | ✅ Done | `apps/ai/client.py`, `requirements/base.txt` |
| **Python 3.14 compatibility** | ✅ Done | `requirements/base.txt` |
| **Dual AI backend (Gemini ↔ Ollama local)** | ✅ Done | `apps/ai/backends/`, `config/settings/base.py` |
| **Scraper location filter bug fix** | ✅ Done | `apps/properties/scrapers/zameen.py`, `graana.py` |
| **Scraper cache key sanitization** | ✅ Done | `apps/properties/scrapers/base.py` → `safe_cache_key()` |
| **Greeting handler (instant, no model call)** | ✅ Done | `apps/ai/agent.py` → `_is_greeting()`, `_greeting_reply()` |
| **Per-message backend logging** | ✅ Done | `apps/whatsapp/router.py` → yellow console line per message |
| **Batch verdicts skip in local mode** | ✅ Done | `apps/properties/search.py` |
| **PostgreSQL migration (from SQLite)** | ✅ Done | `.env` → `DATABASE_URL=postgres://localhost/pakpropai` |
| **Deployment (Render + Supabase + Upstash)** | ❌ Not done | — |
| **Real property data / agent onboarding** | ❌ Not done | — |
| **WhatsApp OTP template (Meta Business Manager)** | ❌ Needs setup | `.env: WA_OTP_TEMPLATE_NAME` |

**Phase 1 completion: 100%** — All code complete. Remaining gaps are operational only.

---

## Phase 2 Checklist

| Feature | Status | File(s) |
|---------|--------|---------|
| Property Audit report (PDF + WhatsApp summary) | ✅ Done | `apps/audit/` — engine, PDF, model, tool, URLs |
| Document OCR flow via WhatsApp | ✅ Done | `apps/ai/agent.py` → `verify_document_image()`, `apps/verification/models.py` → `DocumentScan` |
| Talk to Agent (lead → agent connection) | ✅ Done | `apps/agents/` — model, admin, tool; `apps/ai/tools.py` → `connect_to_agent()`; `apps/ai/agent.py` → `_is_agent_request()`, `_handle_agent_request()` |
| **Property verification improvements** | ✅ Done | See detail below |
| **Verification queue API** (`GET /verification/queue/`) | ✅ Done | `apps/verification/views.py` → `VerificationQueueView` |
| **Admin review API** (`PATCH /verification/queue/<id>/`) | ✅ Done | `VerificationReviewView` — auto-updates `property.legal_status` |
| **Document scan list API** (`GET /verification/documents/`) | ✅ Done | `DocumentScanListView` |
| **Link document to verification** (`POST /verification/documents/<scan>/link/<verif>/`) | ✅ Done | `LinkDocumentToVerificationView` |
| **Signal score engine** (`VerificationSignalService`) | ✅ Done | `apps/verification/services.py` — 0–100 score from OCR confidence, red flags, doc diversity |
| **`reviewer` FK + `signal_score` field on Verification** | ✅ Done | `apps/verification/models.py`, migration `0003` |
| **`verification` FK on DocumentScan** | ✅ Done | Links WhatsApp OCR scans to property verifications |
| **`property.legal_status` auto-update on review** | ✅ Done | passed→verified, failed→unverified, disputed→disputed |
| **Leads API** (`GET /api/v1/leads/`, `PATCH /api/v1/leads/<id>/`) | ✅ Done | `apps/leads/serializers.py`, `views.py`, `urls.py` |
| **Users list API** (`GET /api/v1/auth/users/`, `PATCH /api/v1/auth/users/<id>/`) | ✅ Done | `apps/users/serializers.py` → `UserListSerializer`, `views.py` → `UserListView` |
| **Lead `status` field** (new/warm/qualified/cold) | ✅ Done | `apps/leads/models.py`, migration `0002_add_status_to_lead` |
| **Web login OTP flow** (phone → OTP → JWT → role redirect) | ✅ Done | `pakpropaiweb/src/app/login/page.tsx` — fixed response shape mismatch |
| Agent dashboard (web) | ❌ Not done | — |
| Property scoring improvements (more signals) | ❌ Not done | — |

**Phase 2 completion: ~85%**

---

## Phase 3 Checklist (not started)

| Feature | Status |
|---------|--------|
| Deal Lock (token payment + 48h exclusivity) | ❌ |
| Escrow integration (Safepay / bSecure) | ❌ |
| Developer dashboard | ❌ |
| Admin fraud monitoring dashboard | ❌ |
| Event-driven architecture / microservices extraction | ❌ |

---

## Architecture Decisions Log

| Decision | Reason |
|----------|--------|
| Modular monolith (not microservices) | MVP speed, single deploy, cost |
| Gemini Flash (not GPT-4) | Free tier, sufficient for Pakistani RE context |
| Redis session (not DB) for WA state | Sub-millisecond reads, TTL auto-expiry |
| DB session as audit log alongside Redis | Debugging, compliance, history |
| Batch AI verdicts (1 call for 5 listings) | Cost — avoids N Gemini calls per search |
| Longest-first keyword matching for furnished/construction | Prevents "semi-furnished" matching "furnished" |
| Scrapers run in parallel threads (not Celery tasks) | Simpler for MVP; latency-bound not CPU-bound |
| Scraper results cached 1 hour in Redis | Avoids hammering sites, improves response time |
| Money stored as integer PKR (no floats) | Financial correctness |
| OTP via WhatsApp template (not free-text) | Meta policy: new users are outside 24-h window |
| AI Agent replaces FSM (keyword router removed) | AI handles all conversation natively; more robust, no hardcoded flows |
| Automatic Function Calling (AFC) via google.genai | AI decides which tools to call; no manual dispatch needed |
| Conversation history in Redis (24h TTL, 20 turns) | Persistent context across messages without DB overhead |
| Pakistani RE knowledge in system prompt (not RAG) | Laws rarely change; simpler than vector DB for MVP |
| psycopg2 → psycopg[binary] (psycopg3) | psycopg2 has no Python 3.14 pre-built wheel |
| google.generativeai → google.genai | Old SDK deprecated by Google; new SDK required for continued support |
| Primary model: gemini-2.5-flash-lite | Working free-tier model on this API key (gemini-2.0-flash quota exhausted) |
| Dual backend: Gemini (cloud) + Ollama (local) | Free-tier Gemini limits (20 req/day) block feature testing; local Ollama is unlimited |
| No LangGraph/LangChain for local backend | Simple for-loop handles the Ollama tool-call agentic cycle; OpenAI SDK as HTTP client |
| qwen2.5:7b as local model | Best tool-calling + bilingual (Urdu/English) support at 7B size; fits in 32GB RAM |
| batch_verdicts skipped when AI_BACKEND=local | Prevents stray Gemini API calls when testing locally; verdicts are cosmetic only |
| Scraper location filter as post-fetch word-level match | Zameen/Graana URLs don't support location params; filter client-side. Word match ('DHA Lahore' → ['DHA','Lahore']) handles city-appended queries |
| Greeting handled in code, not by model | qwen2.5:7b ignores system prompt instructions for greetings; hardcoded response is instant, free, and always consistent |
| WhatsApp test accounts restrict recipient numbers | Meta #131030 error — test app allows max 5 pre-approved numbers; add in Meta Dev Portal → WhatsApp → API Setup |
| Audit engine is fully deterministic (no AI call) | Pure rule-based scoring using hardcoded Pakistani RE benchmarks — fast, free, consistent |
| Leads API uses field aliasing in serializer (not model rename) | `score→intent_score`, `city_interest→location_interest` mapped in serializer; model stays unchanged so AI tools are unaffected |
| Verification signal score is deterministic (no AI call) | Computed from confidence, red flag counts, document type diversity — fast, free, consistent across re-runs |
| `property.legal_status` updated directly in review view (not via signal/task) | Simple and synchronous — verification volume is low, no need for async update |
| `IsAdmin` permission class defined inline in verification views | Small enough to not warrant a shared permissions module yet; move to `apps/core/permissions.py` when reused elsewhere |
| Users list lives under `/auth/users/` (not `/users/`) | Keeps all auth-related endpoints under one prefix; admin-only enforced in view, not a separate app |
| Lead `status` is a separate field from `score` | Score is a numeric AI signal (0–100); status is a human-facing lifecycle label (new/warm/qualified/cold) — decoupled so either can change independently |
| DocumentScan model has no property FK | WhatsApp users send docs without having a listed property; standalone model is more flexible |
| Document type detected from caption keywords | Avoids asking user to specify type — natural flow; 12 keyword patterns cover all common docs |
| OCR uses structured prompt format (KEY: VALUE) | Reliable parsing without JSON — local models struggle with strict JSON output |
| SQLite → PostgreSQL (local Homebrew) | Production parity in development; avoids migration surprises at deploy time |
| Django 5.0.6 → 5.2.14 (LTS) | Python 3.14 breaks `context.__copy__()` in Django 5.0.x (admin add/change pages 500); fixed in 5.2; also upgraded django-celery-beat 2.6.0 → 2.9.0 |
| Agent city matching via JSONField icontains | Simple text search on JSON array string representation; avoids separate City model for MVP |
| connect_to_agent strict city match | If city specified but no agent covers it, return "no agent" — never return a wrong-city agent |
| Agent request bypasses model entirely | `_is_agent_request()` detects intent+role word combination in Python; `_handle_agent_request()` calls tool directly — model never involved, hallucination structurally impossible |
| Combination-based agent intent detection | Intent word (connect/find/need/want…) + role word (agent/someone/broker/dealer…) approach handles all natural language variations without enumerated phrase lists |
| Ollama terminal tool bypass | `connect_to_agent` and `generate_property_audit` results returned verbatim from Ollama loop — model cannot rephrase or add hallucinated details |

---

## Known Gaps & Tech Debt

| Item | Priority | Notes |
|------|----------|-------|
| Scraper selectors may break if sites change HTML | Medium | Architecture is modular — just update `_parse_card()` |
| No property deduplication across DB + scrapers | Low | Could show same listing twice; acceptable for MVP |
| Voice transcription depends on Gemini multimodal audio support | Medium | Ollama backend returns '' — user asked to type instead |
| No rate limiting on WhatsApp bot (per user) | Medium | Add Redis-based throttle before going live |
| `WA_APP_SECRET` blank = all webhook signatures accepted (dev) | High | Must be set in production |
| Scraper search is synchronous in request cycle | Medium | Move to Celery task + cache result for heavy traffic |
| Free tier Gemini quota: 20 req/day on gemini-2.5-flash-lite | High | Use AI_BACKEND=local for dev; get paid key for production |
| Agent system prompt is large (~2KB); sent on every request | Low | Acceptable cost for MVP; add prompt caching if volume grows |
| `handlers.py` still exists (FSM flows) but is only used for `_upsert_lead()` | Low | Clean up later; harmless for now |
| WhatsApp test account: max 5 recipient numbers | High | Must add each test number in Meta Dev Portal before it can receive messages |
| Location word-match may over-match on city name | Low | e.g. "Lahore" as a word matches any Lahore result; acceptable since city filter is also applied |
| Document OCR accuracy depends on AI backend | Medium | llava:7b (local) is weak at OCR; Gemini is accurate — use AI_BACKEND=gemini for doc scanning |
| Audit PDF served from local media only | Medium | Needs BASE_URL set to ngrok/production URL for WhatsApp PDF link to be clickable |
| Lead status field is set to 'new' by default — no auto-scoring to warm/qualified/cold yet | Low | Update score→status logic in AI tools when lead scoring is improved |
| Leads API returns all leads to all dashboard roles (no per-agent filtering yet) | Low | Add agent FK to Lead model when agent assignment is built |
| Signal score is only computed when OCR task finishes or admin reviews — not on DocumentScan save | Low | Add post_save signal on DocumentScan to auto-refresh if verification is linked |
| No duplicate property detection in verification signals | Medium | Cross-check same address/owner listed multiple times; add deduction to signal score |
| Agent request detection may miss highly unusual phrasings | Low | Combination-based (intent+role) handles ~95% of cases; truly novel phrasing falls through to model which may still hallucinate — acceptable for MVP |

---

## Environment Variables Required

```
# Core
SECRET_KEY=
DEBUG=False
ALLOWED_HOSTS=

# Database (PostgreSQL)
DATABASE_URL=postgres://localhost/pakpropai          # local
# DATABASE_URL=postgresql://user:pass@host/db        # Supabase/Neon for production

# Redis
REDIS_URL=

# WhatsApp Cloud API
WA_VERIFY_TOKEN=
WA_APP_SECRET=
WA_ACCESS_TOKEN=
WA_PHONE_NUMBER_ID=
WA_OTP_TEMPLATE_NAME=      ← create in Meta Business Manager

# Gemini AI (cloud backend)
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash-lite   # optional, this is the default

# AI Backend switcher
AI_BACKEND=gemini            # 'gemini' (default) or 'local' (Ollama)
LOCAL_MODEL=qwen2.5:7b       # optional, this is the default
LOCAL_VISION_MODEL=llava:7b  # optional
OLLAMA_BASE_URL=http://localhost:11434  # optional

# Base URL (for PDF download links in WhatsApp)
BASE_URL=http://127.0.0.1:8000         # local dev
# BASE_URL=https://yourapp.onrender.com  # production
```

---

## Local Development with Ollama

```bash
# One-time setup
brew install ollama
ollama pull qwen2.5:7b        # 4.7 GB — primary chat + tool-use model
ollama pull llava:7b           # optional — for image/document analysis

# Start Ollama server (keep running in background)
ollama serve

# Switch to local in .env
AI_BACKEND=local

# Run server — you'll see on startup:
# [AI] Backend: LOCAL (Ollama) | model=qwen2.5:7b | url=http://localhost:11434
```

---

## Recommended Next Steps (Priority Order)

1. **Property scoring improvements** — add location tier, price vs benchmark, construction status signals to AI scoring task
2. **Agent listings page (web)** — needs filtering by agent ownership; currently shows all properties
3. **Add real agents via Django admin** — go to /admin → Agents → Add Agent; fill identity, coverage cities, specializations; tick is_verified + is_active
4. **Seed 5–10 real property listings** — so search returns real results during demos
5. **Phase 3: Deal Lock** — token payment + 48h exclusivity flow
6. **Phase 3: Escrow integration** — Safepay/bSecure
7. **Phase 3: Admin fraud monitoring dashboard**
