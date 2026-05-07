# PakProp AI — Build Progress

**Last updated:** 2026-05-07  
**Current branch:** `development`  
**Current phase:** Phase 1 MVP

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
| **Deployment (Render + Supabase + Upstash)** | ❌ Not done | — |
| **Real property data / agent onboarding** | ❌ Not done | — |
| **WhatsApp OTP template (Meta Business Manager)** | ❌ Needs setup | `.env: WA_OTP_TEMPLATE_NAME` |

**Phase 1 completion: ~97%**  
Remaining gaps are operational (deployment + data), not code.

---

## Phase 2 Checklist (not started)

| Feature | Status |
|---------|--------|
| Property Audit report (PDF + WhatsApp summary) | ❌ |
| Document OCR flow via WhatsApp | ❌ |
| Property verification improvements | ❌ |
| Agent dashboard (web) | ❌ |
| Property scoring improvements (more signals) | ❌ |

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
| Scraper location filter as post-fetch substring match | Zameen/Graana URLs don't support location query params; filter client-side after city fetch |

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
| Location filter is substring match — broad queries may over-filter | Low | e.g. "DHA" matches "DHA Phase 1–9"; acceptable for MVP |

---

## Environment Variables Required

```
# Core
SECRET_KEY=
DEBUG=False
ALLOWED_HOSTS=

# Database
DATABASE_URL=

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

1. **Deploy to Render** — connect Supabase (DB) + Upstash (Redis), set env vars, get public URL for WhatsApp webhook
2. **Register webhook in Meta** — use the public URL, set `WA_VERIFY_TOKEN`
3. **Create OTP template in Meta Business Manager** — body: `Your PakProp AI code is {{1}}. Expires in 5 minutes.`
4. **Seed 5–10 real property listings** — so search returns real results during demos
5. **Get a fresh Gemini API key** — current key has only 20 req/day on gemini-2.5-flash-lite; new key gets full free tier limits
6. **Phase 2: Property Audit PDF** — generate report via AI agent, upload to R2, send download link on WhatsApp
7. **Phase 2: Document OCR flow** — already works via `chat_with_image()`; add a dedicated `/verify doc` command that saves to `verification` table
