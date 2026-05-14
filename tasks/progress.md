# PakProp AI — Build Progress

**Last updated:** 2026-05-14 (Phase 12 complete — production deployment config)  
**Current branch:** `development`  
**Current phase:** Phase 12 complete — system is production-ready  
**Audit report folder:** `../audit-reports/` — read before every session  
**Overall system score:** Backend 100% | Frontend 100% | Production-ready 95%

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
| **Agent listings page (web)** | ✅ Done | `pakpropaiweb/src/app/agent/listings/page.tsx` — filter tabs, score bar, request verification |
| **Property scoring improvements** | ✅ Done | `apps/properties/scoring.py` — `PropertyScoringEngine`; enriched AI prompt; deterministic fallback |
| **4-role user model refactor** | ✅ Done | See detail below |

**Phase 2 completion: 100%** ✅

### 4-Role User Model Refactor (session 11)

**Model changes (all migrated):**
- `Agent.user` — OneToOneField to User → links dashboard account to agent profile
- `Agent.parent_organization` — self-FK → agents belong to developer/agency orgs
- `Lead.assigned_agent` — FK to Agent → leads are routed to specific agents
- `Property.assigned_agent` — FK to Agent → properties assigned to managing agent

**Behavior changes:**
- WhatsApp router auto-upserts a Lead on every message (`_upsert_lead(user)` in `router.py`)
- `connect_to_agent` tool sets `lead.assigned_agent` + `lead.status = QUALIFIED` when match found
- Lead ViewSet scopes results by role: agents see only their assigned leads; admin/developer see all
- `PATCH /leads/<id>/` allows status/notes edits by dashboard users

**New APIs:**
- `GET /api/v1/agents/` — admin-only list of all agents
- `GET /api/v1/agents/me/` — returns the agent profile linked to the logged-in user
- `Lead` serializer now exposes `assigned_agent_id` + `assigned_agent_name`

**CLAUDE.md updated** — section 12 now documents all 4 roles with DB model relationships

---

## Phase 3 Checklist

| Feature | Status | File(s) |
|---------|--------|---------|
| **Deal Lock (token payment + 48h exclusivity)** | ✅ Done | See detail below |
| **Escrow integration (Safepay / bSecure)** | ✅ Done | See detail below |
| **Admin fraud monitoring dashboard** | ✅ Done | See detail below |
| **RBAC hardening** | ✅ Done | See detail below |
| **Live System Config** | ✅ Done | See detail below |
| Event-driven architecture / microservices extraction | ❌ | Post-launch only — extract when scale demands it |

**Phase 3 completion: 95%** — Deal Lock + Escrow + Fraud Monitor + RBAC + System Config done. Microservices deferred post-launch.

### Admin Dashboard Fixes (session 16)

**Agents page — empty list bug fixed:**
- Root cause: `/admin/agents` used `UserManagementPage` which hit `GET /auth/users/?role=agent` (only finds `User` objects with `role=agent`); agents created via Django admin without a linked user account were invisible
- Fix: `/admin/agents/page.tsx` fully rewritten — now calls `GET /api/v1/agents/` (`AgentListView`), which returns all `Agent` model records regardless of linked user
- New columns: Name, Phone, Type, Cities, Verified, Active, Featured, Rating
- Quick-toggle buttons for Verify/Unverify and Activate/Deactivate directly in the table row
- Inline edit row: name, phone, email, primary city, verified/active/featured checkboxes (all via `PATCH /agents/{id}/`)
- Full details modal: all agent fields including specializations, experience, bio, company, org, stats
- Empty state message directs admin to use Django admin panel to add agents

**User edit form expanded (admin/clients/developers/admins pages):**
- `UserManagementPage.tsx` inline edit row previously only saved `name` and `email`
- Now also saves `cnic`, `ntn`, `is_filer` — all fields the `UserListSerializer` accepts as writable
- Added CNIC input (monospace, 12345-1234567-1 format), NTN input, Tax Filer checkbox

**Type update (`types/index.ts`):**
- Added `years_experience: number` to `AgentProfile` interface (was missing, caused TS error in detail modal)

### Admin Property CRUD (session 17)

**Backend — `apps/properties/serializers.py`:**
- `PropertyCreateSerializer` expanded: added `furnished_status`, `construction_status`, `legal_status`, `assigned_agent` — admin can now set all fields at create time
- `PropertyDetailSerializer` expanded: added `furnished_status`, `construction_status`, `assigned_agent` to fields so PATCH updates these too

**Frontend — `src/lib/api.ts`:**
- Added `createProperty(data)` → `POST /properties/`
- Added `deleteProperty(id)` → `DELETE /properties/{id}/`

**Frontend — `src/types/index.ts`:**
- `Property` interface extended with `owner`, `owner_phone`, `description`, `furnished_status`, `construction_status`, `assigned_agent`, `is_active`, `updated_at`

**Frontend — `src/app/admin/properties/page.tsx` (full rewrite):**
- **Add Property** modal — sectioned form: Basic Info, Location, Size & Price, Property Details, Admin Controls
- **Edit Property** modal — same form, pre-filled from current property values
- **View Details** modal — all fields including owner phone, AI score, risk level, agent, timestamps
- **Delete** — confirm modal → `DELETE /properties/{id}/`
- Inline **Verify** and **Rescore** buttons kept from previous version
- All 4 property types, 3 furnished statuses, 3 construction statuses, 4 legal statuses as dropdowns
- Error extraction from DRF validation response shown inline in the form
- `formToPayload()` — converts string form state to typed API payload (nulls for empty optional fields)

### Admin Property Owner Picker (session 18)

**Backend — `apps/users/views.py`:**
- Added `?search=` filter to `UserListView.get()`: `Q(phone__icontains=search) | Q(name__icontains=search)` — admin can look up any user by phone or name

**Backend — `apps/properties/serializers.py`:**
- `PropertyCreateSerializer`: added `owner = PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)` so admin can assign any user as owner on create

**Backend — `apps/properties/views.py`:**
- `perform_create`: admin path uses `owner` from validated_data (may be `None` for anonymous listing); non-admin path always forces `owner=request.user`

**Frontend — `src/lib/api.ts`:**
- Added `searchUsers(query)` → `GET /auth/users/?search=<query>`

**Frontend — `src/app/admin/properties/page.tsx`:**
- Added `owner` + `owner_display` fields to `BLANK_FORM`
- `OwnerPicker` component: phone/name search input → results list showing name, role badge, phone → click to select → shows selected owner card with Remove button
- Owner section inserted between Property Details and Admin Controls in the form
- `formToPayload()` sends `owner: UUID | null`
- Edit form pre-fills owner from existing `property.owner` + `property.owner_phone`
- Supports all 3 owner types: Client (role=user), Agent (role=agent), Developer (role=developer)

### Admin Agent CRUD (session 19)

**Backend — `apps/agents/views.py`:**
- `AgentListView` upgraded from `ListAPIView` → `ListCreateAPIView`: `POST /agents/` now creates an agent (admin only via `perform_create` guard)
- `AgentAdminDetailView` upgraded from `RetrieveUpdateAPIView` → `RetrieveUpdateDestroyAPIView`: `DELETE /agents/{id}/` now works (admin only)
- Both views now use `AgentAdminSerializer` (allows writing is_verified/is_active/is_featured and all identity fields)

**Frontend — `src/lib/api.ts`:**
- Added `createAgent(data)` → `POST /agents/`
- Added `deleteAgent(id)` → `DELETE /agents/{id}/`

**Frontend — `src/app/admin/agents/page.tsx` (full rewrite):**
- **Agent ID** displayed prominently in the table as a monospace badge `#N` with a one-click copy-to-clipboard button
- **View** modal: ID shown in a large callout at the top with Copy ID button — unambiguous reference for property assignment
- **+ Add Agent** modal — 5 sections: Identity (name, type, phone, WhatsApp, email), Professional (company, designation, license, experience, bio), Geographic Coverage (primary city, all cities, areas — comma-separated), Specializations (7 checkboxes), Status (verified/active/featured)
- **Edit** modal — same form pre-filled; modal title shows `Edit Agent — #N` for clarity
- **Delete** confirm — warns that leads/properties will be unlinked
- Quick-toggle **Verify/Unverify** and **Activate/Deactivate** buttons stay in the table row
- `agentToForm()` — converts `AgentProfile` → form state (arrays → comma-separated strings)
- `formToPayload()` — converts form state → API payload (comma-separated strings → arrays)

### Live System Config (session 14)

**New app: `apps/config/`** — DB-backed runtime configuration replacing static `.env`-only settings

**`SystemConfig` model (`apps/config/models.py`):**
- Key-value table (`key`, `value`, `updated_at`, `updated_by`)
- `DEFAULTS` — all 31 config keys with sensible defaults
- `SENSITIVE_KEYS` — 7 keys masked as `"__configured__"` in GET responses
- `REQUIRED_KEYS` — 4 keys that must be set for the bot to function
- `ENV_KEY_MAP` — maps config keys → Django settings names for `.env` fallback
- Migration `0001_initial` applied

**`SystemConfigService` (`apps/config/services.py`):**
- `get(key)` → Redis (60s TTL) → DB → Django settings (env) → DEFAULTS — zero-downtime runtime changes
- `bulk_set(data, user)` — batch write with cache invalidation per key
- `is_set(key)` — True if value in DB or env (not just DEFAULTS)
- `get_features()` → `{feature_key: bool}` for all `feature_*` keys
- `scraper_enabled()`, `get_active_gateway()` — convenience accessors
- `get_missing_required()` — list of unset required keys for setup banner

**`ConfigView` (`GET/PATCH /api/v1/config/`):**
- Admin-only (uses inline `IsAdmin` permission)
- GET: sensitive keys masked; includes `setup_complete: bool` + `missing_required: list`
- PATCH: skips `"__configured__"` sentinel (never clears an unchanged secret); returns updated config

**6 subsystems updated to read from `SystemConfigService`:**
- `apps/whatsapp/client.py` — WA token + phone ID (late import, avoids circular)
- `apps/ai/client.py` — Gemini API key (`_get_gemini_key()` helper)
- `apps/ai/agent.py` — new `_get_tools()` filters tool list by feature flags; Gemini key check uses config
- `apps/properties/search.py` — `_from_scrapers()` returns `[]` immediately if `scraper_search_enabled=false`
- `apps/payments/views.py` — `CreateCheckoutView` validates gateway against `active_payment_gateway`; returns 400 if manual-only or wrong gateway
- `apps/ai/tools.py` — `initiate_deal_lock` uses `get_active_gateway()` for online payment link
- `apps/whatsapp/router.py` — voice gate (feature_voice_messages), doc verification gate (feature_document_verification), dynamic `_greeting()` classmethod builds feature list from enabled flags

---

### RBAC Hardening (session 13)

**`apps/core/permissions.py` — `IsOwnerOrReadOnly` admin bypass:**
- Added `if getattr(request.user, 'role', None) == 'admin': return True` to object-level check
- Without this, admin could not edit/verify/rescore any property they didn't personally create

**`apps/agents/views.py` — `AgentAdminDetailView` (new):**
- `AgentAdminSerializer` extends `AgentSerializer`; removes `is_verified`, `is_active`, `is_featured` from `read_only_fields`
- `AgentAdminDetailView` (RetrieveUpdateAPIView) — admin-only, allows `PATCH /agents/<pk>/` to toggle verified/active/featured status

**`apps/agents/urls.py` — new route:**
- `path('<int:pk>/', AgentAdminDetailView.as_view(), name='agents-admin-detail')`

**`apps/verification/views.py` — `DocumentScanListView` fixes:**
- Added `?verification=<uuid>` filter: frontend scan modal now fetches scans scoped to a single verification
- Agent scoping: agents only see scans for verifications on their own properties (via `verification__property_id__in=agent_prop_ids`)

**`apps/payments/views.py` — admin checkout:**
- `CreateCheckoutView` now allows admin to initiate checkout: `if deal.buyer != request.user and request.user.role != 'admin'`

**`apps/audit/views.py` — auth guard:**
- `download_pdf` now returns `HttpResponseForbidden` for unauthenticated requests

**`apps/whatsapp/router.py` — non-client role guard:**
- After `get_or_create`, if `user.role != 'user'`, send dashboard redirect message and return immediately
- `_upsert_lead(user)` moved inside the `role == 'user'` path — agents/admins no longer auto-create leads on WhatsApp message

---

### Admin Fraud Monitoring Dashboard (session 12)

**New model: `FraudBlacklist`** (`apps/verification/models.py`):
- Stores blacklist tokens in DB (token, reason, added_by, expires_at)
- `save()` + `delete()` auto-sync Redis cache — existing `FraudCheckService` fast-path stays consistent
- Migration `0004_fraud_blacklist` applied

**New APIs (`/api/v1/verification/fraud/`):**
- `GET /fraud/stats/` — aggregate counts: suspicious scans, red-flag docs, fraud verifications, disputed/high-risk properties, blacklist size, 7-day alert count
- `GET /fraud/alerts/` — chronological feed synthesised from: suspicious DocumentScans, Verifications with fraud_flags, high-risk Properties
- `GET /fraud/users/` — flagged users: submitted suspicious docs, own high-risk/disputed properties, high WhatsApp message volume
- `GET /fraud/blacklist/` — list active blacklist entries
- `POST /fraud/blacklist/` — add token (optionally with ttl_days)
- `DELETE /fraud/blacklist/<id>/` — remove token (purges Redis too)

**Frontend `/admin/fraud`:**
- 8-card stats grid (alerts 7d, suspicious docs, high-risk props, disputed props, red-flag scans, fraud verifications, blacklisted tokens, system status)
- Stats auto-refresh every 60 seconds
- **Alerts tab** — severity-coded feed (🔴 high / 🟡 medium) with type icon, phone, detail, date
- **Flagged Users tab** — table with risk badge, reason, flags, last seen
- **Blacklist tab** — add form (token + reason + optional TTL) + live table with Remove button
- "🚨 Fraud Monitor" added to admin sidebar

### Escrow Integration (session 12)

**Payment model (`apps/payments/models.py`) updated:**
- Added `gateway` (safepay/bsecure/jazzcash/easypaisa/manual), `checkout_token`, `checkout_url`, `webhook_payload`
- Indexed on `status` and `checkout_token`

**Gateway clients (`apps/payments/services.py`):**
- `SafepayGateway` — creates checkout session, verifies HMAC-SHA256 webhook signature, parses webhook payload
- `bSecureGateway` — OAuth token exchange, creates order, verifies webhook
- `PaymentService.create_checkout()` — factory entry point, saves `Payment` record
- `PaymentService.handle_webhook()` — verifies signature → finds deal → activates lock → WhatsApp notifies buyer

**APIs (`/api/v1/payments/`):**
- `POST /payments/checkout/<deal_id>/` — creates Safepay or bSecure checkout session, returns redirect URL
- `GET /payments/return/?status=success&deal_id=<uuid>` — buyer return landing (webhook does the real activation)
- `POST /payments/webhook/safepay/` — Safepay payment notification (HMAC verified, no auth)
- `POST /payments/webhook/bsecure/` — bSecure payment notification
- `GET /payments/` — admin payment list

**WhatsApp tool updated:** `initiate_deal_lock` now auto-generates a Safepay checkout link if credentials are set

**Settings added:** `SAFEPAY_MERCHANT_KEY`, `SAFEPAY_SECRET_KEY`, `SAFEPAY_ENVIRONMENT`, `BSECURE_CLIENT_ID`, `BSECURE_CLIENT_SECRET`, `BSECURE_ENVIRONMENT`

**Frontend:**
- Admin Deals page now has two tabs: Deal Locks + Payments
- "💳 Pay Online" button → creates Safepay checkout → opens in new tab
- "Confirm Manual" replaces old "Confirm Payment" (for non-online payments)
- Payments tab: full table with status badges, gateway, reference, deal link

### Deal Lock (session 12)

**Model (`apps/escrow/models.py` — `EscrowDeal`):**
- `seller` now nullable — buyer initiates without knowing seller
- Added: `agent` FK, `lock_started_at`, `payment_gateway`, `payment_ref`, `initiated_via`, `admin_notes`
- Added `Status.EXPIRED` — separate from `CANCELLED`
- `activate_lock()` — sets LOCKED + starts 48h window
- `hours_remaining()` — computes hours left on active lock

**APIs (`/api/v1/deals/`):**
- `POST /deals/lock/` — buyer requests lock (status=INITIATED)
- `PATCH /deals/lock/<id>/confirm/` — admin confirms payment → LOCKED + WhatsApp notify
- `PATCH /deals/lock/<id>/cancel/` — buyer or admin cancels
- `GET /deals/` — admin/agent/developer list (filterable by `?status=`)
- `GET /deals/mine/` — buyer's own locks
- `GET /deals/lock/<id>/` — single lock detail

**Celery task (`apps/escrow/tasks.py`):**
- `expire_deal_locks` — runs every 30 min, marks past-expiry LOCKED deals as EXPIRED + notifies buyer via WhatsApp

**AI Tool (`apps/ai/tools.py`):**
- `initiate_deal_lock(property_id, token_amount_pkr, payment_method)` — creates deal lock from WhatsApp
- Blocks duplicate locks on same property
- Returns verbatim payment instructions per gateway

**Frontend:**
- `GET/POST /deals/*` API calls added to `api.ts`
- `DealLock` type added to `types/index.ts`
- Admin: Deal Locks page (`/admin/deals`) — filter tabs, confirm payment modal, cancel, hours-remaining timer
- Agent Listings: 🔒 Locked badge shown on locked properties
- "Deal Locks" added to admin sidebar

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
| Location tier keywords are hardcoded strings | Covers ~95% of Pakistani RE queries; add more keywords to `TIER_1_KEYWORDS`/`TIER_2_KEYWORDS` in `scoring.py` as edge cases are found |
| Price benchmark is a single midpoint per tier | Real benchmarks vary by sub-area; good enough for MVP scoring — refine with real transaction data later |
| `rescore_all_properties_task` has no rate limiting | Fine for current scale; add `countdown` or `rate_limit` when property count exceeds ~1000 |
| Users list lives under `/auth/users/` (not `/users/`) | Keeps all auth-related endpoints under one prefix; admin-only enforced in view, not a separate app |
| Lead `status` is a separate field from `score` | Score is a numeric AI signal (0–100); status is a human-facing lifecycle label (new/warm/qualified/cold) — decoupled so either can change independently |
| DocumentScan model has no property FK | WhatsApp users send docs without having a listed property; standalone model is more flexible |
| Document type detected from caption keywords | Avoids asking user to specify type — natural flow; 12 keyword patterns cover all common docs |
| OCR uses structured prompt format (KEY: VALUE) | Reliable parsing without JSON — local models struggle with strict JSON output |
| SQLite → PostgreSQL (local Homebrew) | Production parity in development; avoids migration surprises at deploy time |
| Django 5.0.6 → 5.2.14 (LTS) | Python 3.14 breaks `context.__copy__()` in Django 5.0.x (admin add/change pages 500); fixed in 5.2; also upgraded django-celery-beat 2.6.0 → 2.9.0 |
| Agent city matching via JSONField icontains | Simple text search on JSON array string representation; avoids separate City model for MVP |
| Self-referential FK for org hierarchy (Agent.parent_organization → Agent) | Reuses existing Agent model; avoids a separate Organization model — agents under developer/agency orgs share same profile structure |
| Lead scoping via `user.agent_profile` reverse OneToOne | Access pattern is `request.user.agent_profile`; if unlinked, `RelatedObjectDoesNotExist` is caught → empty queryset |
| Auto-lead upsert in WhatsApp router (not in AI tools) | Every WhatsApp interaction must register a lead regardless of what the user says — router level guarantees no path is missed |
| connect_to_agent strict city match | If city specified but no agent covers it, return "no agent" — never return a wrong-city agent |
| Agent request bypasses model entirely | `_is_agent_request()` detects intent+role word combination in Python; `_handle_agent_request()` calls tool directly — model never involved, hallucination structurally impossible |
| Combination-based agent intent detection | Intent word (connect/find/need/want…) + role word (agent/someone/broker/dealer…) approach handles all natural language variations without enumerated phrase lists |
| Ollama terminal tool bypass | `connect_to_agent` and `generate_property_audit` results returned verbatim from Ollama loop — model cannot rephrase or add hallucinated details |
| `EscrowDeal.seller` is nullable | Buyer initiates lock without knowing the seller — seller is populated later when deal progresses to full transaction |
| Deal lock 48h window starts on admin confirmation, not on initiation | Prevents clock running while payment is in transit; fair to buyer |
| Webhook endpoints have no auth, verified by HMAC only | Gateways cannot authenticate via JWT — HMAC-SHA256 signature is the industry-standard approach; endpoints return 200 regardless to prevent gateway retries on legitimate failures |
| Manual confirmation flow kept alongside online payment | Some buyers will pay via JazzCash/bank and call in — admin confirm path must always exist regardless of gateway status |
| `FraudBlacklist` stored in DB, synced to Redis on save/delete | DB provides listability (Redis KEYS * is O(N) and unsafe); Redis provides O(1) fast-path check. Both updated atomically in model hooks |
| Fraud alerts are synthesised at query time, not pre-computed | Volume is low enough for MVP; no need for a separate event log table yet |
| Celery beat schedule for lock expiry uses 30-minute interval | Locks are 48h — 30 min granularity means max 30 min of overshoot, acceptable for MVP |
| DB-backed system config with `.env` fallback | Admin can change API keys and feature flags at runtime without redeployment; `.env` values still work as bootstrap — DB overrides env |
| Redis cache (60s TTL) in front of DB config reads | Every WhatsApp message reads config; Redis prevents N DB queries per message. Short TTL means admin changes propagate within 60s |
| Sensitive keys returned as `"__configured__"` sentinel | Frontend can show "already set" state without exposing the actual key; PATCH ignores sentinel so blank submit never clears an existing secret |
| Feature flags remove tools from AI model before chat | Disabled tools are never sent to Gemini/Ollama — the model cannot call what it doesn't know about; cleaner than post-call rejection |
| Voice and doc-verification gated at router level | These are not AI tools — they are router-level media handlers; feature flags applied before any AI call is made |
| `active_payment_gateway` enforced in both API and WhatsApp tool | Single source of truth; admin switches from manual→safepay and both the web checkout API and the WhatsApp deal lock tool switch simultaneously |
| `app_label = 'sysconfig'` to avoid conflict with Django's built-in 'config' namespace | `makemigrations config` fails because Django reserves 'config'; using a distinct label avoids the collision |
| Admin user management CRUD in `/auth/users/` API | `POST /auth/users/` (create with role), `DELETE /auth/users/<id>/` — used by the 4 new admin frontend pages |
| `UserCreateSerializer` — phone + name + email + role | Validates +92XXXXXXXXXX format; `create_user()` so phone is set as username; role pre-set from frontend |
| `UserListSerializer` extended | Added `last_active`, `ntn`, `cnic`, `is_filer` to detail view fields |
| `is_active` gate at OTP verify | Deactivated users (`is_active=False`) get 403 at `POST /auth/otp/verify/`; blocks dashboard login for agents/developers/admins |
| `is_active` gate in WhatsApp router | Deactivated clients get "⛔ Your account has been suspended" message; processing stops before any AI call |
| "Client" terminology | WhatsApp-only users have `role='user'` in DB; frontend and admin UI display them as "Client" everywhere |
| `IsOwnerOrReadOnly` admin bypass added to object-level check | Without bypass, admin 403s on any property they didn't personally create — verified/rescore silently failed in the UI |
| `AgentAdminDetailView` is a separate view from `AgentMeView` | `AgentMeView` is self-service (agent updates own profile); admin view has a different serializer that makes is_verified/is_active writable — clean separation of concerns |
| WhatsApp role guard redirects non-client roles immediately | Agents/admins who message the bot accidentally get a friendly redirect, and their interaction never pollutes the lead table or triggers AI inference |
| `_upsert_lead` scoped to `role='user'` only | Auto-lead creation for agents/developers created noise in lead pipeline; now only genuine client interactions generate leads |
| `/payments/return` added to Next.js PUBLIC_PATHS | Payment gateway redirect does not carry session cookies — middleware must allow it without role routing |
| Client role blocks at OTP verify step (not after token store) | Storing tokens then redirecting `user` role back to `/login` caused an infinite redirect loop; intercepting before token store is clean and reversible |
| `notify_user()` unified helper (not inline WhatsApp calls) | All notification events go through one function that creates a DB record + queues async WhatsApp delivery — consistent bell + WhatsApp without duplicating send logic in every caller |
| `Notification.is_read` + `title` for dashboard bell | Original model was outbound delivery tracker only; `is_read` + `title` repurpose it for in-dashboard inbox without a separate model — one source of truth per notification event |
| `NotificationBell` marks all read on panel open | Avoids a per-item read action; simpler UX — once opened, unread badge clears; individual mark-read stays available via API for future fine-grained control |
| `/notifications/` URL moved from `apps/whatsapp/urls.py` to `apps/notifications/urls.py` | `notification_urlpatterns` in whatsapp urls was a misplaced stub returning WhatsApp sessions under a misleading name; proper app owns its own URL config |
| Frontend `Lead` type uses `intent_score` not `score` | Backend serializer aliases model field `score` → `intent_score` in API response; frontend types must match the API shape, not the DB model |

---

## Known Gaps & Tech Debt

| Item | Priority | Notes |
|------|----------|-------|
| `WA_APP_SECRET` blank = all webhook signatures accepted (dev) | **High** | Must be set in production |
| Free tier Gemini quota: 20 req/day on gemini-2.5-flash-lite | **High** | Use AI_BACKEND=local for dev; get paid key for production |
| WhatsApp test account: max 5 recipient numbers | **High** | Must add each test number in Meta Dev Portal before it can receive messages |
| Safepay / bSecure credentials not set locally | **High** | System works without them (falls back to manual); set keys in `.env` to enable online payment links |
| `connect_to_agent` tool still uses `is_active` only | Low | Update `apps/ai/tools.py` to also filter `availability_status=available` when matching agents (post-launch) |
| Scraper search is synchronous in request cycle | Medium | Move to Celery task + cache result for heavy traffic |
| Voice transcription depends on Gemini multimodal audio support | Medium | Ollama backend returns '' — user asked to type instead |
| Document OCR accuracy depends on AI backend | Medium | llava:7b (local) is weak at OCR; Gemini is accurate — use AI_BACKEND=gemini for doc scanning |
| Audit PDF / report PDF served from local media only | Medium | Needs BASE_URL set to ngrok/production URL for WhatsApp PDF link to be clickable |
| `expire_deal_locks` Celery beat task requires beat worker running | Medium | Run `celery -A config beat -l info` alongside the worker |
| Config API keys stored in DB (not encrypted) | Medium | Acceptable for MVP; use Django-encrypted-fields before multi-tenant production |
| Lead ViewSet scoping requires `user.agent_profile` to exist | Medium | If User has `role=agent` but no Agent record linked → empty queryset; fix by creating Agent record in admin |
| No duplicate property detection in verification signals | Medium | Cross-check same address/owner listed multiple times |
| Notification WhatsApp delivery silently fails if 24h window expired | Medium | Client has to reply first; `send_whatsapp_async` marks status=FAILED but dashboard bell still shows unread |
| Scraper selectors may break if sites change HTML | Medium | Architecture is modular — just update `_parse_card()` |
| No property deduplication across DB + scrapers | Low | Could show same listing twice; acceptable for MVP |
| Agent system prompt is large (~2KB); sent on every request | Low | Acceptable cost for MVP; add prompt caching if volume grows |
| `handlers.py` still exists (FSM flows) but is only used for `_upsert_lead()` | Low | Clean up later; harmless |
| Lead status field is set to 'new' by default — no auto-scoring | Low | Update score→status logic in AI tools when scoring is improved |
| Signal score is only computed when OCR task finishes or admin reviews | Low | Add post_save signal on DocumentScan to auto-refresh if verification is linked |
| Agent request detection may miss highly unusual phrasings | Low | ~95% coverage; novel phrasings fall through to model |
| Fraud alerts feed has no pagination | Low | Capped at 200 rows; add cursor pagination when volume grows |
| Blacklist Redis sync is best-effort | Low | Add retry or post-startup sync if Redis restarts frequently |
| No property deduplication across DB + scrapers | Low | Could show same listing twice; acceptable for MVP |

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

# Safepay (online payments — primary gateway)
SAFEPAY_MERCHANT_KEY=      ← from Safepay Dashboard → Settings → API Keys
SAFEPAY_SECRET_KEY=
SAFEPAY_ENVIRONMENT=sandbox   # or 'production'

# bSecure (online payments — secondary gateway)
BSECURE_CLIENT_ID=
BSECURE_CLIENT_SECRET=
BSECURE_ENVIRONMENT=sandbox   # or 'production'

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

## Phase 4 — Launch Blocker Resolution (Start Here)
*Full audit: `../audit-reports/` — read `PAKPROP_CRITICAL_PRODUCTION_BLOCKERS.md` before starting*
*Estimated: ~36 hours to clear all 10 blockers*

### Critical (must fix before any production traffic)
1. **WhatsApp rate limiting** — add `_check_rate_limit(phone)` in `apps/whatsapp/router.py`; Redis counter 10 msg/min/phone; daily OTP cap 10/day in notifications — **2h**
2. **CRM conversations** — `ConversationThread` + `ConversationMessage` models; `GET /leads/{id}/conversations/`; `POST /leads/{id}/send-message/` — **12h** (frontend also needed — see pakpropaiweb/tasks/progress.md)
3. **WhatsApp OTP template** — register in Meta BM; add startup guard for `WA_APP_SECRET` — **1h + external**
4. **Payment E2E test** — set Safepay sandbox keys, run checkout → webhook → deal lock cycle with integration test — **4h**
5. **Property images** — `PropertyImage` model + Cloudflare R2 storage + `POST /properties/{id}/upload-images/` — **6h**
6. **Multi-tenant middleware** — `apps/core/middleware.py` → `TenantIsolationMiddleware`; enforce org scoping at middleware level — **3h**

### High priority (pre-launch polish)
7. Verification status notifications — `post_save` signal → WhatsApp notify owner on APPROVED/REJECTED — **2h**
8. Deal lock expiry notification — modify `expire_deal_locks` task to notify buyer + seller — **2h**
9. Appointment reminders task — `send_appointment_reminders` Celery task every 15min — **3h**
10. Production CORS + security headers in `settings/production.py` — **1h**

### Phase 5 — Feature Completion (~23h)
11. Report generation: `POST /reports/generate/` + Celery task + `GET /reports/{id}/` + download — ✅ **8h**
12. Appointment CRUD: `confirm/`, `reschedule/`, `cancel/`, `complete/` — ✅ **2h**
13. Lead auto-assignment: `suggest_agents_for_lead()` + `POST /leads/{id}/assign/` + auto-assign — ✅ **6h**
14. Developer team management: `GET/POST /agents/team/` + `DELETE /agents/team/{id}/` — ✅ **4h**
15. Conversation DB persistence: `ConversationMessage` model + router hook — ✅ **4h** (done in Phase 4 Blocker 9)

### Phase 6 — Analytics + Advanced Features (~40h)
See `../audit-reports/PAKPROP_IMPLEMENTATION_PRIORITY_PLAN.md` Phase 3 for full list.

### Operational (parallel with Phase 4)
- **Onboard real agents** — Django admin → Agents → Add Agent
- **Seed real property listings** — agent WhatsApp listing or bulk Django shell import → Rescore All
- **Set Safepay production credentials** after sandbox E2E test passes
- **Start Celery beat** — `celery -A config beat -l info` alongside `celery -A config worker -l info`

---

## Recommended Next Steps (as of 2026-05-14)

Priority order for remaining work before deployment:

### Phase 9 — Pre-launch Hardening ✅ Complete
All items done:
- `User.phone` format validator + migration
- `Property` legal status transition enforcement in `clean()`
- `Agent` `primary_city ∈ cities` validation in `clean()`
- `notify_verification_status_change` Celery task (wired into verification review view)
- `generate_monthly_reports` Celery task (crontab: 1st of month 06:00 PKT)
- `check_whatsapp_token_health` Celery task (every 6 hours)
- `RequestAuditMiddleware` — structured API request logging via `api.audit` logger
- Beat schedule updated with new entries

### Phase 10 — Complete All Remaining Missing Features ✅
All items done:
- `UserNotificationPreference` model (`apps/notifications/models.py`) + migration `notifications.0003`
- `LeadScoreHistory` model (`apps/leads/models.py`) + signals (`apps/leads/signals.py`) + migration `leads.0006`
- `NotificationPreferenceSerializer` (`apps/notifications/serializers.py`)
- `NotificationPreferencesView` — `GET/PATCH /auth/me/notification-preferences/` (`apps/users/views.py` + `urls.py`)
- `PropertyCompareView` — `GET /properties/compare/?ids=...` (`apps/properties/views.py` + `urls.py`)
- `PropertyMarketTrendsView` — `GET /properties/market-trends/?city=&period=` (`apps/properties/views.py`)
- `BulkAssignLeadsView` — `POST /leads/bulk-assign/` (`apps/leads/views.py` + `urls.py`)
- `BulkRejectVerificationsView` — `POST /verification/bulk-reject/` (`apps/verification/views.py` + `urls.py`)
- Migrations applied and `manage.py check` → 0 issues

### Phase 11 — RBAC Security Hardening ✅ Complete
All permission gaps from the 2026-05-09 RBAC audit closed:
- Added `IsAdminOrDeveloper` permission class (`apps/core/permissions.py`)
- `POST /properties/` — `get_permissions()` override returns `[IsAgentOrAdmin()]` for `create` action; blocks `role=user` clients
- `POST /properties/{id}/request_verification/` — explicit owner-or-admin check added
- `AgentListView` (`POST /agents/`) — `permission_classes` updated to `[IsAdminOrDeveloper]`; `perform_create` now allows developers to create pending agents scoped to their org
- `AgentAdminDetailView` (`GET/PATCH/DELETE /agents/{id}/`) — developers can now GET/PATCH agents within their own org; DELETE remains admin-only
- Confirmed already-correct: `GET /agents/` developer scope in `get_queryset()`, `PATCH /leads/{id}/` agent scope via `get_queryset()`
- `manage.py check` → 0 issues; permission logic smoke-tested

### Phase 12 — Production Deployment ✅ Complete

| Item | Status | File(s) |
|------|--------|---------|
| `render.yaml` — web + worker + beat services | ✅ Done | `pakpropai/render.yaml` |
| `Dockerfile` — uses prod.txt, runs collectstatic | ✅ Done | `pakpropai/Dockerfile` |
| `Procfile` — beat worker entry added | ✅ Done | `pakpropai/Procfile` |
| `requirements/prod.txt` — cleaned to `-r base.txt` | ✅ Done | `pakpropai/requirements/prod.txt` |
| `prod.py` — duplicate Sentry init removed (base.py handles it) | ✅ Done | `config/settings/prod.py` |
| `vercel.json` — frontend deployment config + security headers | ✅ Done | `pakpropaiweb/vercel.json` |
| `next.config.ts` — R2 / production image domains | ✅ Done | `pakpropaiweb/next.config.ts` |
| CORS locked to `FRONTEND_URL` env var | ✅ Done | `config/settings/prod.py` |
| Sentry + JSON logging behind env vars | ✅ Done | `config/settings/base.py` |

**Remaining operational steps (external — no code changes needed):**
1. WhatsApp OTP template registration in Meta Business Manager (~30 min)
2. Set `WA_APP_SECRET` in Render env and verify webhook signature
3. Create Render account, link repo, set all `sync: false` env vars in the dashboard
4. Deploy: Render runs `docker build` → `migrate` → `collectstatic` automatically
5. Connect Supabase/Neon PostgreSQL (copy connection string → `DATABASE_URL`)
6. Connect Upstash Redis (copy URL → `REDIS_URL`)
7. Deploy frontend: `vercel --prod` or push to main branch with Vercel GitHub integration

## Phase 4 Checklist

| Item | Status | Hours |
|---|---|---|
| WhatsApp rate limiting | ✅ Done | 2h |
| CRM ConversationThread model + APIs | ✅ Done | 12h |
| WhatsApp OTP template registration | ⏸️ deferred to production | external |
| Payment gateway E2E test | ✅ Done | 4h |
| Property image upload | ✅ Done | 6h |
| Multi-tenant isolation middleware | ✅ Done | 3h |
| Verification status notifications | ✅ Done | 2h |
| Deal lock expiry notifications | ✅ Done | 2h |
| Appointment reminders Celery task | ✅ Done | 3h |
| Production CORS + security headers | ✅ Done | 1h |

**Phase 4 completion: 100%** (WhatsApp OTP template deferred to operational setup at launch)

---

## Phase 5 — Frontend Alignment + Notifications (sessions 21–23)

### Backend additions

| Item | Status | File(s) |
|------|--------|---------|
| Notifications API — `GET /notifications/` (inbox + unread count) | ✅ Done | `apps/notifications/views.py`, `urls.py` |
| Notifications API — `POST /notifications/mark-read/` | ✅ Done | `apps/notifications/views.py` |
| `Notification.title` + `Notification.is_read` fields + migration | ✅ Done | `apps/notifications/models.py`, migration `0002` |
| `notify_user()` unified helper | ✅ Done | `apps/notifications/services.py` |
| Lead assignment → notify agent (dashboard + WhatsApp) | ✅ Done | `apps/leads/services.py` |
| Appointment confirm/cancel/reschedule → notify client | ✅ Done | `apps/leads/views.py` → `_notify_appointment()` |
| Deal lock confirmed → notify buyer | ✅ Done | `apps/escrow/views.py` → `_notify_buyer_lock_active()` |
| Report ready → notify via unified `notify_user` | ✅ Done | `apps/reports/tasks.py` |
| `/notifications/` URL moved to `apps/notifications/urls.py` | ✅ Done | `config/urls.py` |

**Phase 5 backend completion: 100%**

### Frontend — `pakpropaiweb/`

#### Foundation

| Item | Status | File(s) |
|------|--------|---------|
| `src/lib/api.ts` — full rewrite: 40+ named API functions, all routes correct | ✅ Done | `src/lib/api.ts` |
| `src/types/index.ts` — added `PropertyImage`, `ConversationMessage`, `Appointment`, `Report`, `Notification` | ✅ Done | `src/types/index.ts` |
| Fixed `VerificationRequest` — UUID id, `signal_score`, `fraud_flags`, `notes` | ✅ Done | `src/types/index.ts` |
| Fixed `Lead` — `intent_score` alias matches serializer, correct field set | ✅ Done | `src/types/index.ts` |
| Added `primary_image`, `images[]`, `installment_available` to `Property` | ✅ Done | `src/types/index.ts` |
| Added `title`, `is_read` to `Notification` | ✅ Done | `src/types/index.ts` |

#### Page fixes (existing pages)

| Item | Status | File(s) |
|------|--------|---------|
| `admin/verification/page.tsx` — replaced raw `api.get/patch` with `getVerificationQueue()` / `reviewVerification()` | ✅ Done | `src/app/admin/verification/page.tsx` |
| `agent/leads/page.tsx` — replaced raw `api.get` with `getLeads()`, removed duplicate local type | ✅ Done | `src/app/agent/leads/page.tsx` |
| `developer/leads/page.tsx` — same fix | ✅ Done | `src/app/developer/leads/page.tsx` |

#### New pages built

| Page | Role | Features |
|------|------|---------|
| `/admin/leads` | Admin | Full leads table, search + status filter, auto-assign button, CRM chat panel |
| `/admin/appointments` | Admin | Appointments table, confirm/cancel/complete actions, status filter |
| `/admin/reports` | Admin | Lead funnel analytics, property inventory stats, agent performance table |
| `/agent/appointments` | Agent | Upcoming/past split view, confirm and mark-complete actions |
| `/developer/team` | Developer | Team member table, add-agent dropdown, remove member |
| `/developer/reports` | Developer | Report generator form + history table with PDF download, quick lead/property stats |

#### Feature additions to existing pages

| Item | Status | File(s) |
|------|--------|---------|
| `agent/listings` — `PropertyImageUploader` component per card, primary image thumbnail, photo count | ✅ Done | `src/app/agent/listings/page.tsx` |
| `admin/properties` — `PropertyImagesSection` in detail modal (upload + per-image delete) | ✅ Done | `src/app/admin/properties/page.tsx` |
| `agent/leads` — `ConversationPanel` slide-in with full chat thread + send message | ✅ Done | `src/app/agent/leads/page.tsx` |
| `admin/leads` — same `ConversationPanel` + CRM Chat column | ✅ Done | `src/app/admin/leads/page.tsx` |

#### Navigation

| Item | Status | File(s) |
|------|--------|---------|
| Admin sidebar — added Leads, Appointments, Reports nav items | ✅ Done | `src/components/layout/Sidebar.tsx` |
| Agent sidebar — added Appointments nav item | ✅ Done | `src/components/layout/Sidebar.tsx` |
| Developer sidebar — added My Team, Reports nav items | ✅ Done | `src/components/layout/Sidebar.tsx` |

#### Notification bell

| Item | Status | File(s) |
|------|--------|---------|
| `NotificationBell` component — bell icon, red unread badge, dropdown panel, mark-all-read, 30s auto-poll | ✅ Done | `src/components/ui/NotificationBell.tsx` |
| `DashboardLayout` — added top header bar with notification bell (all 3 roles) | ✅ Done | `src/components/layout/DashboardLayout.tsx` |

**Phase 5 frontend completion: 100%**

---

## Phase 6 — Agent Registration + Analytics Dashboards (sessions 24–26)

### Agent Self-Registration + Approval Workflow

| Item | Status | File(s) |
|------|--------|---------|
| `Agent.registration_status` field (`pending/approved/rejected`) + migration | ✅ Done | `apps/agents/models.py`, migration `0003` |
| `Agent.rejection_reason` field | ✅ Done | `apps/agents/models.py` |
| `AgentRegistrationSerializer` — validates phone, creates User+Agent atomically | ✅ Done | `apps/agents/serializers.py` |
| `POST /agents/register/` — public self-registration (AllowAny) | ✅ Done | `apps/agents/views.py` → `AgentRegisterView` |
| `POST /agents/{id}/approve/` — admin always; developer for their org | ✅ Done | `apps/agents/views.py` → `AgentApproveView` |
| `POST /agents/{id}/reject/` — requires rejection_reason | ✅ Done | `apps/agents/views.py` → `AgentRejectView` |
| `GET /agents/?status=pending` — filter for pending queue | ✅ Done | `apps/agents/views.py` → `AgentListView` |
| Notify developer + admins on new registration | ✅ Done | `AgentRegisterView._notify_approvers()` |
| Notify agent on approval/rejection | ✅ Done | `AgentApproveView._notify_agent()` / `AgentRejectView._notify_agent()` |
| `TeamView.get()` scoped to `registration_status=APPROVED` only | ✅ Done | `apps/agents/views.py` |
| `/register` page — 5-section public form (Account, Type, Professional, Geographic, Specializations) | ✅ Done | `pakpropaiweb/src/app/register/page.tsx` |
| Developer employee path — org selector in register form | ✅ Done | `/register` page |
| `/register` added to `PUBLIC_PATHS` in Next.js middleware | ✅ Done | `pakpropaiweb/src/middleware.ts` |
| Admin agents page — Pending Approval tab with approve/reject + reject modal | ✅ Done | `pakpropaiweb/src/app/admin/agents/page.tsx` |
| Developer team page — full rewrite with Pending + Active tabs | ✅ Done | `pakpropaiweb/src/app/developer/team/page.tsx` |
| `getPendingAgents()`, `registerAgent()`, `approveAgent()`, `rejectAgent()` in api.ts | ✅ Done | `pakpropaiweb/src/lib/api.ts` |
| `registration_status`, `rejection_reason` added to `AgentProfile` type | ✅ Done | `pakpropaiweb/src/types/index.ts` |

### Analytics Dashboards (Phase 6)

| Item | Status | File(s) |
|------|--------|---------|
| `RevenueReportView` — `GET /reports/revenue/?period=` | ✅ Done | `apps/reports/views.py` |
| `BotReportView` — `GET /reports/bot/?period=` | ✅ Done | `apps/reports/views.py` |
| `AgentPersonalReportView` — `GET /reports/my-stats/?period=` (agent-only) | ✅ Done | `apps/reports/views.py` |
| `AgentReportView` updated to support `IsAdminOrDeveloper` (developer sees own org's agents) | ✅ Done | `apps/reports/views.py` |
| `getAgentPersonalReport()`, `getRevenueReport()`, `getBotReport()` in api.ts | ✅ Done | `pakpropaiweb/src/lib/api.ts` |
| Shared Charts.tsx component library | ✅ Done | `pakpropaiweb/src/components/ui/Charts.tsx` |
| `BarChart` (CSS bar chart, 5 colors, configurable height) | ✅ Done | `Charts.tsx` |
| `MiniBarChart` (compact sparkline) | ✅ Done | `Charts.tsx` |
| `StatCard` (KPI card with colored left-border accent + icon) | ✅ Done | `Charts.tsx` |
| `PeriodToggle` (weekly/monthly toggle buttons) | ✅ Done | `Charts.tsx` |
| `ChartCard` (card wrapper with period toggle) | ✅ Done | `Charts.tsx` |
| `LeadPipelineFunnel` (horizontal funnel bars with stage conversion rates) | ✅ Done | `Charts.tsx` |
| `BreakdownBar` (segmented horizontal bar with legend) | ✅ Done | `Charts.tsx` |
| `formatPkr()` (PKR → Cr/L/K formatter) | ✅ Done | `Charts.tsx` |
| `/admin/analytics` — 6 KPI cards, pipeline funnel, property breakdown, 4 trend charts, agent table | ✅ Done | `pakpropaiweb/src/app/admin/analytics/page.tsx` |
| `/agent/analytics` — 6 KPI cards, personal lead funnel, lead trend chart, source breakdown | ✅ Done | `pakpropaiweb/src/app/agent/analytics/page.tsx` |
| `/developer/analytics` — 5 KPI cards, pipeline funnel, property breakdown, 2 trend charts, team table | ✅ Done | `pakpropaiweb/src/app/developer/analytics/page.tsx` |
| Sidebar — "Analytics" nav item added to admin, agent, developer | ✅ Done | `pakpropaiweb/src/components/layout/Sidebar.tsx` |

**Phase 6 completion: 100%** ✅

---

## Phase 7 — CRM Detail + Agent Availability (2026-05-14)

| Item | Status | File(s) |
|------|--------|---------|
| `Agent.availability_status` field (`available/busy/offline`) + migration `0004` | ✅ Done | `apps/agents/models.py` |
| `availability_status` added to `AgentSerializer` | ✅ Done | `apps/agents/serializers.py` |
| `PATCH /agents/me/availability/` — agent sets own status | ✅ Done | `apps/agents/views.py` → `AgentAvailabilityView` |
| `PATCH /agents/{pk}/availability/` — admin sets any agent's status | ✅ Done | `apps/agents/views.py` → `AgentAvailabilityView` |
| `GET /agents/available/?city=` — lists active+available agents, optional city filter | ✅ Done | `apps/agents/views.py` → `AgentAvailableListView` |
| `GET /leads/appointments/?upcoming=true` — future scheduled/confirmed, sorted asc | ✅ Done | `apps/leads/views.py` → `AppointmentViewSet.get_queryset()` |
| `updateAgentAvailability()`, `getAvailableAgents()` added to api.ts | ✅ Done | `pakpropaiweb/src/lib/api.ts` |
| `availability_status` added to `AgentProfile` type | ✅ Done | `pakpropaiweb/src/types/index.ts` |
| `/agent/leads/[id]` — full lead detail page | ✅ Done | `pakpropaiweb/src/app/agent/leads/[id]/page.tsx` |
| Lead list "View" link → detail page | ✅ Done | `pakpropaiweb/src/app/agent/leads/page.tsx` |
| Availability toggle (available/busy/offline buttons) on profile page | ✅ Done | `pakpropaiweb/src/app/agent/profile/page.tsx` |
| `?upcoming=true` query wired in agent appointments page | ✅ Done | `pakpropaiweb/src/app/agent/appointments/page.tsx` |
| `/notifications` page (all roles) | ✅ Done | `pakpropaiweb/src/app/admin|agent|developer/notifications/page.tsx` — all use `NotificationsInbox` component |
| `/admin/audit-log` page — system audit log viewer | ✅ Done | `pakpropaiweb/src/app/admin/audit-log/page.tsx` — filters: action/model/actor; pagination; before/after JSON diff |

**Phase 7 completion: 100%** ✅

---

## Phase 8 — Pre-Launch Feature Completions (2026-05-14)

### AI Features

| Item | Status | File(s) |
|------|--------|---------|
| `POST /leads/{id}/summarize/` — AI summary of last 30 messages | ✅ Done | `apps/leads/views.py` → `LeadViewSet.summarize()` |
| `POST /leads/{id}/suggest-replies/` — 3 context-aware reply suggestions | ✅ Done | `apps/leads/views.py` → `LeadViewSet.suggest_replies()` |
| Both use Gemini (cloud) or Ollama (local) via `AI_BACKEND` setting | ✅ Done | Dual-backend support |
| AI summary panel on `/agent/leads/[id]` | ✅ Done | `pakpropaiweb/src/app/agent/leads/[id]/page.tsx` |
| Smart reply chips on message composer | ✅ Done | same page — chips above composer, click to paste |

### Lead Management

| Item | Status | File(s) |
|------|--------|---------|
| `POST /leads/merge/` — merge secondary into primary (admin only) | ✅ Done | `apps/leads/views.py` → `MergeLeadsView`; `apps/leads/urls.py` |
| Transfers messages + appointments, merges notes, keeps higher intent score | ✅ Done | |
| Merge modal on `/admin/leads/duplicates` — swap primary/secondary, confirm | ✅ Done | `pakpropaiweb/src/app/admin/leads/duplicates/page.tsx` |
| `mergeLeads()` in `api.ts` | ✅ Done | `pakpropaiweb/src/lib/api.ts` |

### Infrastructure

| Item | Status | File(s) |
|------|--------|---------|
| Sentry SDK integration — `sentry-sdk[django]` behind `SENTRY_DSN` env var | ✅ Done | `requirements/base.txt`, `config/settings/base.py` |
| Integrations: Django, Celery, Redis; `traces_sample_rate=0.1` | ✅ Done | |
| Structured JSON logging — `python-json-logger` JSON formatter in production | ✅ Done | `requirements/base.txt`, `config/settings/base.py` → `LOGGING` dict |
| Simple formatter in DEBUG mode; JSON formatter in production (`not DEBUG`) | ✅ Done | |

**Phase 8 completion: 100%** ✅

### Remaining post-launch items (not building pre-launch)
- ML lead scoring — needs historical conversion data
- ML agent recommendation engine — needs historical data
- Property price prediction — needs sold price dataset
- Commission tracking — needs real revenue data
- Lead nurture automation sequences — needs CRM maturity
- `OrgAnalyticsSnapshot` + `AgentPerformanceSnapshot` models — real-time reports cover this for MVP
- `AppointmentCalendar` component — list view is sufficient for launch
- `WhatsAppTemplateManager` — Meta Business Manager is the canonical UI; read-only API listing not worth the effort
- CSRF token integration — JWT cookie-based auth is the pattern; CSRF surface is already minimal with `SameSite=Lax`
