# PakProp AI — Build Progress

**Last updated:** 2026-05-06  
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
| Multi-step conversation state machine (FSM) | ✅ Done | `apps/whatsapp/sessions.py`, `router.py` |
| Tax (7E) advisory flow | ✅ Done | `apps/whatsapp/handlers.py` |
| Loan eligibility flow | ✅ Done | `apps/whatsapp/handlers.py` |
| Lead capture (auto on every completed flow) | ✅ Done | `apps/whatsapp/handlers.py` → `apps/leads/models.py` |
| Property listing via WhatsApp (5-step guided) | ✅ Done | `apps/whatsapp/handlers.py` |
| Property search — DB listings | ✅ Done | `apps/properties/search.py` |
| Property search — web scrapers (Zameen, Graana, OLX) | ✅ Done | `apps/properties/scrapers/` |
| Modular scraper registry (add sites in one line) | ✅ Done | `apps/properties/scrapers/registry.py` |
| Paginated search results (3 per message, "more" to continue) | ✅ Done | `apps/properties/search.py` |
| AI batch verdicts on search results | ✅ Done | `services/ai_orchestrator.py` → `batch_verdicts()` |
| Voice message transcription (Gemini multimodal) | ✅ Done | `apps/ai/client.py`, `apps/whatsapp/router.py` |
| Furnished / unfurnished / semi-furnished filter | ✅ Done | `apps/properties/models.py`, `handlers.py` |
| Builder / ready / under-construction filter | ✅ Done | `apps/properties/models.py`, `handlers.py` |
| AI scoring task (async, Celery) | ✅ Done | `apps/properties/tasks.py` |
| **Deployment (Render + Supabase + Upstash)** | ❌ Not done | — |
| **Real property data / agent onboarding** | ❌ Not done | — |
| **WhatsApp OTP template (Meta Business Manager)** | ❌ Needs setup | `.env: WA_OTP_TEMPLATE_NAME` |

**Phase 1 completion: ~90%**  
The two remaining gaps are operational (deployment + data), not code.

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

---

## Known Gaps & Tech Debt

| Item | Priority | Notes |
|------|----------|-------|
| Scraper selectors may break if sites change HTML | Medium | Architecture is modular — just update `_parse_card()` |
| No property deduplication across DB + scrapers | Low | Could show same listing twice; acceptable for MVP |
| Voice transcription depends on Gemini multimodal audio support | Medium | Test with real `.ogg` files from WhatsApp |
| No rate limiting on WhatsApp bot (per user) | Medium | Add Redis-based throttle before going live |
| `WA_APP_SECRET` blank = all webhook signatures accepted (dev) | High | Must be set in production |
| Scraper search is synchronous in request cycle | Medium | Move to Celery task + cache result for heavy traffic |

---

## Environment Variables Required

```
# Core
SECRET_KEY=
DEBUG=False
ALLOWED_HOSTS=
DATABASE_URL=

# Redis
REDIS_URL=

# WhatsApp Cloud API
WA_VERIFY_TOKEN=
WA_APP_SECRET=
WA_ACCESS_TOKEN=
WA_PHONE_NUMBER_ID=
WA_OTP_TEMPLATE_NAME=      ← create in Meta Business Manager

# Gemini AI
GEMINI_API_KEY=
```

---

## Recommended Next Steps (Priority Order)

1. **Deploy to Render** — connect Supabase (DB) + Upstash (Redis), set env vars, get public URL for WhatsApp webhook
2. **Register webhook in Meta** — use the public URL, set `WA_VERIFY_TOKEN`
3. **Create OTP template in Meta Business Manager** — body: `Your PakProp AI code is {{1}}. Expires in 5 minutes.`
4. **Seed 5–10 real property listings** — so search returns real results during demos
5. **Phase 2: Document OCR flow** — user sends property doc image → Gemini extracts fields → verification record created
6. **Phase 2: Property Audit PDF** — generate report, upload to R2, send download link on WhatsApp
