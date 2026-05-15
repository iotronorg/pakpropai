# PakProp AI — Backend Architecture Documentation

> **Perspective:** Architect · Tech Lead · QA Engineer  
> **Version:** 2026-05-15  
> **Repository:** `pakpropai/` — the core Django backend

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Tech Stack](#2-tech-stack)
3. [Project Layout](#3-project-layout)
4. [Django Apps — Responsibilities](#4-django-apps--responsibilities)
5. [Data Models & Relationships](#5-data-models--relationships)
6. [API Design](#6-api-design)
7. [Authentication & Session Management](#7-authentication--session-management)
8. [RBAC — Role-Based Access Control](#8-rbac--role-based-access-control)
9. [Middleware Stack](#9-middleware-stack)
10. [WhatsApp Integration Layer](#10-whatsapp-integration-layer)
11. [AI Orchestration Layer](#11-ai-orchestration-layer)
12. [Async Processing — Celery Task Map](#12-async-processing--celery-task-map)
13. [Storage Strategy](#13-storage-strategy)
14. [Configuration & Feature Flags](#14-configuration--feature-flags)
15. [Security Architecture](#15-security-architecture)
16. [Observability & Logging](#16-observability--logging)
17. [Request Lifecycle](#17-request-lifecycle)
18. [QA — Known Risks & Testing Surface](#18-qa--known-risks--testing-surface)
19. [Deployment Model](#19-deployment-model)

---

## 1. System Overview

PakProp AI is a **WhatsApp-first trust infrastructure layer** for Pakistani real estate. The backend is a **modular Django monolith** — a single deployable unit with cleanly separated internal apps. It provides:

- A WhatsApp AI assistant (conversational, voice, OCR)
- Property search, verification, and AI scoring
- Lead capture and CRM pipeline
- Deal locking with escrow-adjacent payment coordination
- A REST API consumed by the Next.js web dashboard

### Core Product Loop (Everything Maps to This)

```
DISCOVER → VERIFY → DECIDE → CONNECT → TRANSACT
   ↓           ↓        ↓         ↓          ↓
Properties  Scam   AI Score  Agent     Deal Lock
 Search    Check  Tax/Loan  Match     + Payment
```

### Deployment topology (current)

```
Internet
   │
   ├── Meta WhatsApp Cloud API  ──POST──▶  /api/v1/whatsapp/webhook/
   │
   └── Next.js Dashboard  ─────HTTP──▶  /api/v1/*
                                              │
                                       Django (Gunicorn/ASGI)
                                              │
                                    ┌─────────┴─────────┐
                                    │                   │
                                 PostgreSQL           Redis
                                 (Supabase/Neon)    (Upstash)
                                                       │
                                                   Celery Workers
                                                       │
                                              ┌────────┴────────┐
                                           Gemini API     Cloudflare R2
                                         (AI calls)      (File storage)
```

---

## 2. Tech Stack

| Layer | Technology | Rationale |
|---|---|---|
| Framework | Django 5.x + DRF | Mature, batteries-included, fast to build on |
| API | Django REST Framework | Serializers, ViewSets, JWT integration |
| Auth | `rest_framework_simplejwt` | HTTP-only cookie + Bearer header support |
| Database | PostgreSQL (Supabase/Neon) | Relational integrity for leads/escrow/RBAC |
| Cache / Broker | Redis (Upstash free tier) | Sessions, rate-limit counters, AI cache, task broker |
| Async Tasks | Celery + `django_celery_beat` | Periodic jobs, heavy async work |
| Task Result Storage | `django_celery_results` (DB backend) | Result persistence, no extra infra |
| AI | Gemini 2.5 Flash Lite (+ Ollama fallback) | Low cost, vision + text + audio |
| File Storage | Cloudflare R2 (S3-compatible) | Cost-effective; falls back to local disk in dev |
| WhatsApp | Meta Cloud API | Official channel, free tier sufficient for MVP |
| Payments | Safepay (primary) + bSecure (secondary) | Pakistani gateway, escrow-compatible |
| Error Tracking | Sentry (optional) | Disabled by default; enable via `SENTRY_DSN` env var |
| CORS/Static | `corsheaders` + `whitenoise` | Zero-config static serving |
| Logging | `python-json-logger` | Structured JSON logs in production |
| Settings | `django-environ` | `.env` based, environment-aware |

---

## 3. Project Layout

```
pakpropai/
├── config/
│   ├── settings/
│   │   ├── base.py          ← All shared settings (JWT, Celery, cache, storage, AI)
│   │   ├── dev.py           ← DEBUG=True, debug toolbar
│   │   └── prod.py          ← Security headers, HTTPS, HSTS
│   ├── urls.py              ← Root URL dispatcher
│   ├── celery.py            ← Celery app definition
│   ├── asgi.py
│   └── wsgi.py
│
├── apps/
│   ├── core/                ← Cross-cutting concerns (middleware, permissions, validators)
│   ├── users/               ← Custom User model, OTP auth, JWT, role management
│   ├── agents/              ← Agent profiles, teams, verification, availability
│   ├── properties/          ← Property CRUD, AI scoring, image upload, search
│   ├── leads/               ← Lead CRM, appointments, conversation log, scoring
│   ├── verification/        ← OCR, document scans, fraud check, blacklist
│   ├── escrow/              ← Deal lock lifecycle, 48h exclusivity window
│   ├── payments/            ← Safepay/bSecure checkout, webhooks, payment records
│   ├── whatsapp/            ← Webhook handler, message router, session management
│   ├── ai/                  ← Agent tools, Gemini client, AI interaction log
│   ├── notifications/       ← Notification records, delivery queue, preferences
│   ├── audit/               ← Property audit PDF generation
│   ├── reports/             ← Analytics dashboards, async report generation
│   └── config/              ← SystemConfig model, feature flags, API key management
│
├── services/
│   ├── ai_orchestrator.py   ← Structured AI calls (score, tax, OCR, fraud, loan)
│   └── prompt_library.py    ← Jinja2-style prompt templates for all AI tasks
│
└── tasks/
    ├── progress.md          ← Session-level build tracking
    └── lessons.md           ← Lessons learned log
```

---

## 4. Django Apps — Responsibilities

### `core`
- `LastActiveMiddleware` — throttled DB writes for `User.last_active`
- `TenantIsolationMiddleware` — attaches `request.agent_profile` + `request.tenant_org`
- `RequestAuditMiddleware` — structured JSON log of every `/api/` request
- `permissions.py` — `IsOwnerOrReadOnly`, `IsAgentOrAdmin`, `IsAdminOrDeveloper`
- `throttles.py` — per-endpoint rate limit keys (OTP, AI, search, fraud, bulk, reports)
- `validators.py` — shared field validators (phone, CNIC, price)

### `users`
- Custom `User` model extending `AbstractBaseUser` (UUID PK, phone as username, role enum)
- `OTPCode` model — 6-digit codes, 5-minute expiry, 5-attempt lockout
- OTP send → OTP verify → JWT issue flow
- `JWTCookieOrHeaderAuthentication` — dual-mode: HTTP-only cookie (dashboard) or `Authorization: Bearer` header (API clients)
- Refresh token rotation + blacklisting via `simplejwt`
- `UserListView` — admin-only user management

### `agents`
- `Agent` model — polymorphic record covering individual agents, agencies, and developers
- `parent_organization` self-FK — org hierarchy (developer → agents)
- `registration_status` — pending/approved/rejected approval workflow
- `availability_status` — real-time availability for lead routing
- Performance counters: `total_leads`, `closed_deals`, `rating`
- `AgentRegisterView` — unauthenticated registration endpoint
- `AgentApproveView` / `AgentRejectView` — admin-only approval actions

### `properties`
- `Property` model — UUID PK, full listing data, AI score, risk level, legal status
- Legal status FSM — enforced state transitions (unverified → pending → verified/disputed)
- `PropertyImage` — multiple images per property, ordered, stored in R2
- `PropertyScoringEngine` (`scoring.py`) — deterministic signals + Gemini AI score
- `PropertySearchService` (`search.py`) — DB query + live scraper fallback
- Custom actions: `request_verification`, `rescore`, `upload-images`, `compare`, `market-trends`

### `leads`
- `Lead` — user + intent + budget + score + agent assignment
- `LeadActivity` — append-only timeline log (created, assigned, status, note, scored, deal_lock)
- `Appointment` — scheduled visits, status FSM (scheduled → confirmed → completed/cancelled)
- `ConversationMessage` — CRM message log (inbound/outbound, WhatsApp/dashboard)
- `LeadScoreHistory` — immutable audit trail for every score change
- Signals: auto-create Lead on first WhatsApp message (`leads/signals.py`)
- Custom actions: `assign`, `auto-assign`, `summarize`, `suggest-replies`, `send-message`, `duplicate-check`, `bulk-assign`, `merge`

### `verification`
- `DocumentScan` — OCR results (extracted fields, red flags, confidence, raw text)
- `Verification` — property verification request + reviewer assignment + fraud flags
- `FraudBlacklist` — tokens with Redis-sync on save/delete for fast-path checks
- `FraudCheckService` — blacklist fast-path → Gemini deep analysis
- Bulk reject, queue management, fraud stats/alerts/flagged-users endpoints

### `escrow`
- `EscrowDeal` — full deal lock state machine (initiated → locked → released/cancelled/expired)
- `LOCK_DURATION_HOURS = 48` exclusivity window
- `UniqueConstraint` — one active lock per property at a time
- `activate_lock()` — sets `lock_started_at` + `lock_expires_at` + status = locked
- Celery beat task: `expire_deal_locks` every 30 minutes

### `payments`
- `Payment` — linked to `EscrowDeal`, tracks gateway/ref/status
- Safepay + bSecure checkout creation and webhook handlers
- HMAC signature verification on webhooks
- `PaymentReturnView` — redirect URL for browser-based payment flows

### `whatsapp`
- `WhatsAppSession` — persistent audit log (Redis = live state; DB = history)
- `WhatsAppMessage` — full message log (all types: text, audio, image, document)
- `WhatsAppWebhookView` — Meta signature verification, idempotency (48h Redis key)
- `MessageRouter` — dispatches incoming messages to `PakPropAgent`
- Celery: token health check every 6 hours

### `ai`
- `AIInteraction` model — log of every Gemini call (interaction_type, tokens, latency)
- `GeminiClient` — wrapper around `google.generativeai`: `generate_json`, `vision`, `transcribe_audio`
- `tools.py` — 9 tool functions available to the AI agent:
  - `search_properties` — DB + scraper search
  - `calculate_7e_tax` — 7E annual tax computation
  - `check_loan_eligibility` — Apna Ghar loan check
  - `run_fraud_check` — description → risk score + reasoning
  - `generate_property_audit` — full audit with PDF output
  - `list_property` — voice/text → structured listing creation
  - `connect_to_agent` — match + WhatsApp deep-link to agent
  - `initiate_deal_lock` — start token payment flow
- `PakPropAgent` — stateful conversational agent with tool-use loop

### `notifications`
- `Notification` — per-user notification records (title, message, channel, status, is_read)
- `UserNotificationPreference` — per-user toggles (whatsapp, sms, email, lead_updates, etc.)
- Celery: retry failed notifications every 30 minutes

### `audit`
- `PropertyAudit` — AI-generated property audit report
- `pdf.py` — PDF report generation using ReportLab/WeasyPrint
- Admin can download any audit; agents can download their own

### `reports`
- `Report` — async report record (status: pending → generating → ready → failed)
- Analytics views: leads, agents, properties, revenue, bot, agent-personal-stats
- Async generation via Celery; monthly reports auto-generated on the 1st

### `config`
- `SystemConfig` — single-row settings table (feature flags, API keys, gateway selection)
- Read by AI agent and services at runtime — allows toggling features without deploy
- Admin-only write access; sensitive values redacted in read responses

---

## 5. Data Models & Relationships

```
User (UUID)
 ├── role: client | agent | developer | admin
 ├── OTPCode (1:M)  — auth codes
 ├── agent_profile (0:1) → Agent
 ├── leads (1:M) → Lead
 ├── properties (1:M) → Property (as owner)
 ├── wa_sessions (1:M) → WhatsAppSession
 └── document_scans (1:M) → DocumentScan

Agent
 ├── user (0:1) → User (dashboard login)
 ├── parent_organization (0:1) → Agent (self-FK, org hierarchy)
 ├── team_members (1:M) → Agent (reverse of parent_organization)
 ├── assigned_leads (1:M) → Lead
 ├── assigned_properties (1:M) → Property
 ├── appointments (1:M) → Appointment
 └── deal_locks (1:M) → EscrowDeal

Property (UUID)
 ├── owner (0:1) → User
 ├── assigned_agent (0:1) → Agent
 ├── images (1:M) → PropertyImage
 ├── verifications (1:M) → Verification
 ├── escrow_deals (1:M) → EscrowDeal
 └── appointments (1:M) → Appointment

Lead (UUID)
 ├── user (1) → User
 ├── assigned_agent (0:1) → Agent
 ├── activities (1:M) → LeadActivity
 ├── appointments (1:M) → Appointment
 ├── messages (1:M) → ConversationMessage
 └── score_history (1:M) → LeadScoreHistory

Verification (UUID)
 ├── property (1) → Property
 ├── requested_by (0:1) → User
 ├── reviewer (0:1) → User
 └── document_scans (1:M) → DocumentScan

EscrowDeal (UUID)
 ├── property (1) → Property [PROTECT]
 ├── buyer (1) → User [PROTECT]
 ├── seller (0:1) → User [PROTECT]
 ├── agent (0:1) → Agent
 └── payments (1:M) → Payment

WhatsAppSession (UUID)
 ├── phone (indexed)
 ├── user (0:1) → User
 └── messages (1:M) → WhatsAppMessage

SystemConfig (single row)
 └── All feature flags, API keys, gateway config
```

### Key Constraints & Integrity Rules

| Model | Constraint | Purpose |
|---|---|---|
| `User.phone` | `UNIQUE` | One account per number |
| `Agent.user` | `OneToOneField` | One dashboard login per agent |
| `Agent.phone` | `UNIQUE` | No duplicate agents |
| `Agent.primary_city` | `clean()` validation | Must be in `cities` list |
| `Property.price_pkr` | `clean()` validation | Must be > 0 |
| `Property.legal_status` | FSM transitions in `clean()` | Prevents illegal status jumps |
| `Lead.budget_min/max` | `clean()` validation | min ≤ max |
| `EscrowDeal` | `UniqueConstraint(status__in=['initiated','locked'])` | One active lock per property |
| `OTPCode` | `is_valid()`: not used, < 5 attempts, not expired | Prevents brute-force |
| `FraudBlacklist` | Redis-sync on save/delete | Cache stays consistent with DB |

---

## 6. API Design

### Base Path: `/api/v1/`

All endpoints follow REST conventions. Responses are JSON. Pagination is `PageNumberPagination` at 20 items per page.

### Endpoint Map

```
AUTH
  POST  /auth/csrf/                        — Fetch CSRF token (dashboard login init)
  POST  /auth/otp/send/                    — Send OTP to phone
  POST  /auth/otp/verify/                  — Verify OTP → issue JWT cookies
  POST  /auth/token/refresh/               — Rotate access token via refresh cookie
  GET   /auth/me/                          — Current user profile
  POST  /auth/logout/                      — Clear JWT cookies + blacklist tokens
  GET   /auth/users/                       — [admin] List all users
  PATCH /auth/users/<uuid>/                — [admin] Update user
  GET   /auth/me/notification-preferences/ — Get notif preferences
  PATCH /auth/me/notification-preferences/ — Update notif preferences

AGENTS
  GET   /agents/                           — [admin/dev] List all agents
  GET   /agents/me/                        — [agent] Own profile
  PATCH /agents/me/                        — [agent] Update own profile
  PATCH /agents/me/availability/           — [agent] Set availability status
  GET   /agents/available/                 — List available agents (for routing)
  POST  /agents/register/                  — [public] Agent self-registration
  GET   /agents/team/                      — [developer] Own team members
  POST  /agents/team/                      — [developer] Add team member
  DELETE/agents/team/<id>/                 — [developer] Remove team member
  GET   /agents/<id>/                      — [admin] Agent detail
  POST  /agents/<id>/approve/              — [admin] Approve agent
  POST  /agents/<id>/reject/               — [admin] Reject agent

PROPERTIES
  GET   /properties/                       — List (scoped by role)
  POST  /properties/                       — Create new property
  GET   /properties/<uuid>/               — Property detail
  PATCH /properties/<uuid>/               — Update (owner or admin)
  DELETE/properties/<uuid>/               — Delete (owner or admin)
  GET   /properties/mine/                  — [agent] Own listings only
  POST  /properties/<uuid>/request_verification/ — Request verification
  POST  /properties/<uuid>/rescore/        — Trigger AI rescore
  POST  /properties/<uuid>/upload-images/  — Upload images (multipart)
  DELETE/properties/<uuid>/images/<imgid>/ — Delete image
  POST  /properties/rescore-all/           — [admin] Rescore all properties
  GET   /properties/compare/              — Compare up to 4 properties
  GET   /properties/market-trends/        — Price trends by city

LEADS
  GET   /leads/                            — List (scoped: admin=all, agent=assigned)
  POST  /leads/                            — Create lead
  GET   /leads/<uuid>/                     — Lead detail
  PATCH /leads/<uuid>/                     — Update status/notes
  POST  /leads/<uuid>/assign/              — Assign agent
  POST  /leads/<uuid>/auto-assign/         — AI-assisted assignment
  GET   /leads/<uuid>/suggest-agents/      — Suggest matched agents
  GET   /leads/<uuid>/conversations/       — CRM message history
  POST  /leads/<uuid>/send-message/        — Send outbound CRM message
  POST  /leads/<uuid>/summarize/           — AI summary of lead
  POST  /leads/<uuid>/suggest-replies/     — AI reply suggestions
  GET   /leads/duplicates/                 — Duplicate lead detection
  POST  /leads/bulk-assign/               — Bulk assign to agent
  POST  /leads/merge/                      — Merge two leads
  GET   /leads/appointments/              — List appointments
  POST  /leads/appointments/              — Create appointment
  GET   /leads/appointments/<uuid>/        — Appointment detail
  PATCH /leads/appointments/<uuid>/        — Update appointment
  POST  /leads/appointments/<uuid>/confirm/    — Confirm
  POST  /leads/appointments/<uuid>/reschedule/ — Reschedule
  POST  /leads/appointments/<uuid>/cancel/     — Cancel
  POST  /leads/appointments/<uuid>/complete/   — Mark complete

VERIFICATION
  POST  /verification/fraud-check/         — Fraud risk check on any text/link
  GET   /verification/queue/               — [admin] Pending verifications
  PATCH /verification/queue/<uuid>/        — [admin] Approve/reject verification
  POST  /verification/bulk-reject/         — [admin] Bulk reject
  GET   /verification/documents/           — Document scan list
  GET   /verification/documents/<id>/      — Document scan detail
  POST  /verification/documents/<id>/link/<vid>/ — Link scan to verification
  GET   /verification/fraud/stats/         — [admin] Fraud statistics
  GET   /verification/fraud/alerts/        — [admin] Live fraud alerts
  GET   /verification/fraud/users/         — [admin] Flagged users
  GET   /verification/fraud/blacklist/     — [admin] Blacklist
  POST  /verification/fraud/blacklist/     — [admin] Add blacklist token
  DELETE/verification/fraud/blacklist/<id>/ — [admin] Remove token

DEALS (ESCROW)
  GET   /deals/                            — [admin] All deal locks
  GET   /deals/mine/                       — [client] Own deal locks
  POST  /deals/lock/                       — Initiate deal lock
  GET   /deals/lock/<uuid>/                — Deal detail
  PATCH /deals/lock/<uuid>/confirm/        — [admin] Confirm payment
  PATCH /deals/lock/<uuid>/cancel/         — Cancel deal lock
  PATCH /deals/lock/<uuid>/seller-confirm/ — Seller confirmation

PAYMENTS
  GET   /payments/                         — [admin] All payments
  POST  /payments/checkout/<deal_uuid>/    — Create gateway checkout session
  GET   /payments/return/                  — Post-payment redirect handler
  POST  /payments/webhook/safepay/         — Safepay webhook (public)
  POST  /payments/webhook/bsecure/         — bSecure webhook (public)

WHATSAPP
  GET   /whatsapp/webhook/                 — Meta verification challenge
  POST  /whatsapp/webhook/                 — Incoming message delivery (public)

AUDIT
  GET   /audit/                            — List property audits
  GET   /audit/download/<id>/              — Download PDF (scoped to owner/admin)

REPORTS
  GET   /reports/leads/                    — Lead analytics dashboard
  GET   /reports/agents/                   — Agent analytics dashboard
  GET   /reports/properties/              — Property analytics dashboard
  GET   /reports/revenue/                  — Revenue analytics dashboard
  GET   /reports/bot/                      — WhatsApp bot usage report
  GET   /reports/my-stats/                 — [agent] Personal performance stats
  POST  /reports/generate/                 — Async report generation
  GET   /reports/mine/                     — User's own generated reports
  GET   /reports/<uuid>/                   — Report status
  GET   /reports/<uuid>/download/          — Download generated report

NOTIFICATIONS
  GET   /notifications/                    — List user notifications
  POST  /notifications/mark-read/          — Mark notifications as read

CONFIG
  GET   /config/                           — [admin] System configuration
  PATCH /config/                           — [admin] Update config / feature flags
```

### Throttle Rates

| Key | Rate | Applied To |
|---|---|---|
| `anon` | 30/min | Unauthenticated requests |
| `user` | 120/min | All authenticated users |
| `otp_send` | 3/hour | OTP send endpoint |
| `otp_daily` | 10/day | OTP send (daily cap) |
| `ai_query` | 10/min | AI-powered endpoints |
| `fraud_check` | 20/min | Fraud check endpoint |
| `property_search` | 30/min | Property search |
| `report_generate` | 5/hour | Report generation |
| `bulk_operation` | 10/min | Bulk assign, bulk reject |
| `score_property` | 15/min | AI property rescoring |

---

## 7. Authentication & Session Management

### OTP Authentication Flow

```
Client                     Backend                    WhatsApp
  │                            │                          │
  │── POST /auth/otp/send/ ──▶ │                          │
  │     { phone }              │── Send OTP via WA API ──▶│
  │                            │   (or SMS fallback)      │
  │◀─── 200 { detail } ───────│                          │
  │                            │                          │
  │── POST /auth/otp/verify/ ▶ │  (validates OTPCode:     │
  │     { phone, code }        │   not used, < 5 attempts,│
  │                            │   not expired)           │
  │                            │── Creates/gets User ────▶│
  │◀─── 200 { user, role } ───│                          │
  │     Set-Cookie:            │                          │
  │       access_token (15min) │                          │
  │       refresh_token (7d)   │                          │
  │       user_role (JS-read)  │                          │
```

### JWT Cookie Spec

| Cookie | Value | Flags | TTL |
|---|---|---|---|
| `access_token` | JWT | `HttpOnly`, `SameSite=Lax` | 15 minutes |
| `refresh_token` | JWT | `HttpOnly`, `SameSite=Lax` | 7 days |
| `user_role` | role string | `SameSite=Lax` (JS-readable for middleware routing) | 7 days |
| `csrftoken` | CSRF token | `SameSite=Lax` (JS-readable for `X-CSRFToken` header) | session |

### Refresh Flow

Axios interceptor on the frontend catches `401` responses and calls `POST /auth/token/refresh/` automatically. Refresh tokens rotate on every use and the old token is blacklisted.

### `JWTCookieOrHeaderAuthentication`

Custom DRF authentication class that tries:
1. `HTTP-only access_token cookie` (dashboard users)
2. `Authorization: Bearer <token>` header (API/WhatsApp bot flows)

---

## 8. RBAC — Role-Based Access Control

### Role Hierarchy

```
admin
  └── developer (org-level)
        └── agent (execution-level)
              └── client (WhatsApp only)
```

### Data Access Scoping

| Resource | client | agent | developer | admin |
|---|---|---|---|---|
| Leads | own only | assigned only | all org leads | all |
| Properties | own only | own listings | all org listings | all |
| Agents | — | own profile | org team | all |
| Verification | — | own properties | org properties | all |
| Fraud stats | — | — | — | all |
| Config | — | — | — | all |
| Users | — | — | — | all |
| Analytics | — | personal stats | org-wide | platform-wide |

### Permission Classes

```python
IsOwnerOrReadOnly       # Safe methods always allowed; writes require ownership or admin
IsAgentOrAdmin          # role in ('agent', 'admin', 'developer')
IsAdminOrDeveloper      # role in ('admin', 'developer')
```

### Tenant Isolation

`TenantIsolationMiddleware` attaches on every request:
- `request.agent_profile` — the linked `Agent` record (or None)
- `request.tenant_org` — for developers: themselves; for agents: their `parent_organization`

ViewSets use these to filter querysets before returning any data.

---

## 9. Middleware Stack

Execution order (top to bottom on request, bottom to top on response):

```
1. SecurityMiddleware            ← HTTPS redirect, HSTS (prod only)
2. CorsMiddleware                ← CORS headers (localhost:3000 allowed)
3. WhiteNoiseMiddleware          ← Compressed static file serving
4. SessionMiddleware             ← Session support
5. CommonMiddleware              ← Trailing slash, content-length
6. CsrfViewMiddleware            ← CSRF token validation
7. AuthenticationMiddleware      ← Django session auth (admin panel)
8. LastActiveMiddleware          ← Update user.last_active (throttled via Redis)
9. TenantIsolationMiddleware     ← Attach agent_profile + tenant_org
10. RequestAuditMiddleware       ← Structured audit log for /api/ paths
11. MessageMiddleware            ← Flash message support
12. XFrameOptionsMiddleware      ← Clickjacking protection
```

---

## 10. WhatsApp Integration Layer

### Incoming Message Processing

```
Meta Cloud API
  │
  POST /api/v1/whatsapp/webhook/
  │
  WhatsAppWebhookView
  ├── [1] HMAC signature verify (sha256 with WA_APP_SECRET)
  ├── [2] Idempotency check (Redis key: wa:processed:{msg_id}, TTL=48h)
  ├── [3] Parse payload → extract message dict
  └── [4] MessageRouter.dispatch(message)
            │
            ├── Get/create WhatsAppSession (state machine)
            ├── Get/create User (auto-create client on first message)
            ├── Auto-create Lead (signals.py → LeadActivity.CREATED)
            ├── Log WhatsAppMessage to DB
            │
            └── PakPropAgent.process(message, session, user)
                      │
                      ├── Restore context from Redis session
                      ├── Handle message type:
                      │     text   → direct to Gemini with tools
                      │     audio  → transcribe_audio → text → Gemini
                      │     image  → vision OCR → DocumentScan → Gemini
                      │     doc    → vision OCR → DocumentScan → Gemini
                      │
                      ├── Gemini function-calling loop (tools.py)
                      │     search_properties → PropertySearchService
                      │     calculate_7e_tax  → AIOrchestrator.tax_7e
                      │     check_loan_eligibility → AIOrchestrator.loan_eligibility
                      │     run_fraud_check   → FraudCheckService
                      │     generate_property_audit → PropertyAudit + PDF
                      │     list_property     → Property.objects.create
                      │     connect_to_agent  → Agent match + WA deep-link
                      │     initiate_deal_lock → EscrowDeal.objects.create
                      │
                      └── Send reply via Meta Cloud API
```

### Session State Machine

States: `IDLE` → `SEARCH` → `VERIFY` → `LISTING` → `DEAL_LOCK` → etc.

Live state in Redis (JSON context blob). DB `WhatsAppSession` is the audit log, updated on session close or state transitions.

### Outgoing Messages

The AI agent calls `WhatsAppSender.send_text()` / `send_template()` / `send_media()` which hit the Meta Graph API with the configured `WA_ACCESS_TOKEN` and `WA_PHONE_NUMBER_ID`.

OTP delivery uses a pre-approved Meta template (`WA_OTP_TEMPLATE_NAME`). Within the 24-hour conversation window, free-form text is used.

---

## 11. AI Orchestration Layer

### Two AI Entry Points

| Entry Point | Used For | File |
|---|---|---|
| `PakPropAgent` | WhatsApp conversational AI with tool-use loop | `apps/ai/agent.py` |
| `AIOrchestrator` | Structured one-shot AI calls (score, OCR, tax, fraud) | `services/ai_orchestrator.py` |

### AI Backends

Configured via `AI_BACKEND` env var:
- `gemini` (default) — Gemini 2.5 Flash Lite via `google.generativeai`
- `local` — Ollama (`qwen2.5:7b` text, `llava:7b` vision) for zero-cost dev

### Caching Strategy

All deterministic AI calls are cached in Redis:

| Call Type | Cache TTL | Key |
|---|---|---|
| Intent classify | 1 hour | `ai:intent:{sha256(message)}` |
| Property score | request-time (not cached) | — |
| Tax 7E | 24 hours | `ai:tax7e:{sha256(inputs)}` |
| Loan eligibility | 24 hours | `ai:loan:{sha256(inputs)}` |
| Fraud check | 1 hour | `ai:fraud:{sha256(query)}` |

### Prompt Library

All prompts live in `services/prompt_library.py` as named templates:
`intent_classify`, `property_score`, `tax_7e_advisor`, `loan_eligibility`, `fraud_check`, `ocr_property_doc`, `batch_verdicts`

This centralizes prompt versioning and makes A/B testing straightforward.

### `AIInteraction` Logging

Every Gemini call is logged to the `ai_interactions` table with:
- `interaction_type` — classify/score/tax/loan/fraud/ocr/conversation
- `input_tokens`, `output_tokens`
- `latency_ms`
- `user` FK — for per-user usage tracking and billing attribution

---

## 12. Async Processing — Celery Task Map

### Worker Configuration

```
CELERY_BROKER_URL = Redis (Upstash)
CELERY_RESULT_BACKEND = django-db (django_celery_results)
CELERY_BEAT_SCHEDULER = DatabaseScheduler (django_celery_beat)
```

### Scheduled Tasks (Celery Beat)

| Task | Schedule | App |
|---|---|---|
| `expire_deal_locks` | Every 30 min | escrow |
| `rescore_all_properties_task` | Every 24h | properties |
| `mark_stale_leads` | Every 24h | leads |
| `send_stale_lead_reminders` | Every 24h | leads |
| `refresh_agent_performance_snapshots` | Every 24h | agents |
| `send_appointment_reminders` | Every 15 min | leads |
| `retry_failed_notifications` | Every 30 min | notifications |
| `check_whatsapp_token_health` | Every 6h | whatsapp |
| `generate_monthly_reports` | 1st of month, 06:00 PKT | reports |

### Async On-Demand Tasks (triggered by API calls)

| Task | Trigger |
|---|---|
| Property AI scoring | `POST /properties/<id>/rescore/` |
| Document OCR processing | WhatsApp image/doc message |
| PDF audit generation | `POST /audit/` |
| Report generation | `POST /reports/generate/` |
| Notification delivery (WA/SMS/email) | Lead events, deal updates, appointments |

---

## 13. Storage Strategy

### Development (default)

Django `MEDIA_ROOT` → local disk. Files served via `WhiteNoise` when `DEBUG=True`.

### Production (Cloudflare R2)

Activated automatically when `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET_NAME` are set:

```python
STORAGES = {
    'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
    ...
}
AWS_S3_ENDPOINT_URL = f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com'
```

### File Types and Paths

| Type | Path Pattern | Storage |
|---|---|---|
| Property images | `property_images/{property_uuid}/{uuid}.{ext}` | R2 |
| Agent photos | `agents/photos/{filename}` | R2 |
| Document uploads (OCR) | `documents/{uuid}.{ext}` | R2 |
| Generated PDFs (audits) | `audits/{audit_id}.pdf` | R2 |
| Generated reports | `reports/{report_id}.pdf` | R2 |
| Static assets | `staticfiles/` | WhiteNoise (served from web process) |

---

## 14. Configuration & Feature Flags

The `SystemConfig` model (single-row) controls the system at runtime without code changes.

### Feature Flags (toggleable without deploy)

| Flag | Default | Controls |
|---|---|---|
| `feature_property_search` | true | WhatsApp search tool availability |
| `feature_property_listing` | true | AI property listing via WA |
| `feature_tax_advice` | true | 7E + CGT tax tool |
| `feature_loan_eligibility` | true | Apna Ghar loan check |
| `feature_scam_check` | true | Fraud check tool |
| `feature_document_verification` | true | OCR + verification |
| `feature_property_audit` | true | Premium audit PDF |
| `feature_talk_to_agent` | true | Agent matching + connection |
| `feature_deal_lock` | true | Escrow deal lock |
| `feature_voice_messages` | true | Audio transcription |
| `scraper_search_enabled` | false | Live Zameen/Graana/OLX scraping |
| `active_payment_gateway` | manual | safepay \| bsecure \| manual |

### Setup Completeness Check

`GET /config/` returns `setup_complete: bool` and `missing_required: []` so the admin dashboard can surface a setup wizard.

---

## 15. Security Architecture

### Input Validation

- Phone: `RegexValidator(^\+?[0-9]{10,15}$)`
- CNIC: `RegexValidator(^\d{5}-\d{7}-\d$)`
- Price: `clean()` — must be positive
- Budget: `clean()` — min ≤ max
- Legal status transitions: FSM enforced in `Property.clean()`
- `Agent.primary_city` must be in `Agent.cities` list

### Authentication Hardening

- OTP: 5-attempt lockout, 5-minute expiry, 3/hour rate limit
- JWT access tokens: 15-minute TTL
- Refresh tokens: rotate on every use, blacklisted on rotation/logout
- `WA_APP_SECRET` HMAC verification on every incoming webhook

### API Security

- CSRF: `CSRF_COOKIE_HTTPONLY=False` (JS reads `csrftoken`), `CSRF_COOKIE_SAMESITE=Lax`
- CORS: only `localhost:3000` and production domain allowed; credentials required
- Rate limiting: per-endpoint throttles (see §6)
- `RequestAuditMiddleware`: every `/api/` call logged (method, path, user, role, status, ms)

### Data Isolation

- `TenantIsolationMiddleware` enforces agent/developer scoping at the request level
- ViewSets apply additional queryset filters:
  - Agent: `lead.assigned_agent == request.agent_profile`
  - Developer: `property.owner.agent_profile.parent_organization == request.tenant_org`
  - Admin: no filter

### Fraud Prevention

- `FraudBlacklist` with Redis fast-path lookup on every fraud check
- Blacklist tokens sync to Redis immediately on `save()` / `delete()`
- Duplicate lead detection before saving new leads from WhatsApp
- 48-hour message idempotency on webhook (prevents replay)

---

## 16. Observability & Logging

### Log Format (production)

```json
{
  "asctime": "2026-05-15 14:32:01",
  "name": "api.audit",
  "levelname": "INFO",
  "message": "POST /api/v1/leads/ user=abc-123 role=agent status=201 42ms"
}
```

### Loggers

| Logger | Level | Purpose |
|---|---|---|
| `root` | INFO | Catch-all |
| `django` | WARNING | Framework internals only |
| `django.request` | ERROR | Request errors |
| `api.audit` | INFO | Every API request (via RequestAuditMiddleware) |
| `apps` | INFO | Application-level events |

### Sentry Integration

Optional. Enable by setting `SENTRY_DSN`. Integrations: Django, Celery, Redis. Sample rate: 10%. PII disabled.

### AI Interaction Logging

Every Gemini call writes an `AIInteraction` record with input/output token counts and latency. Used for cost monitoring and per-user attribution.

---

## 17. Request Lifecycle

### REST API Request (Dashboard → Backend)

```
Browser (Next.js)
  │
  ├── Axios interceptor: attach X-CSRFToken header (mutating requests)
  │
  POST /api/v1/leads/<id>/assign/
  │
  Middleware chain:
    SecurityMiddleware
    CorsMiddleware → validate Origin header
    SessionMiddleware
    CsrfViewMiddleware → validate X-CSRFToken
    AuthenticationMiddleware
    LastActiveMiddleware → (async) update user.last_active
    TenantIsolationMiddleware → attach agent_profile, tenant_org
    RequestAuditMiddleware → start timer
  │
  DRF Router → LeadViewSet.assign()
    ├── JWTCookieOrHeaderAuthentication → validate access_token cookie
    ├── IsAuthenticated permission check
    ├── IsAgentOrAdmin permission check
    ├── get_queryset() → filter by tenant scope
    ├── Business logic (assign agent, create LeadActivity)
    └── Return 200 JSON response
  │
  RequestAuditMiddleware → log timing + status
```

### WhatsApp Message (Inbound)

```
Meta Cloud API → POST /api/v1/whatsapp/webhook/
  ├── AllowAny (no JWT required)
  ├── HMAC signature verify
  ├── Idempotency check (Redis)
  ├── MessageRouter.dispatch() → synchronous
  │     ├── Auto-create User (if new phone)
  │     ├── Auto-create Lead (signal)
  │     ├── Log WhatsAppMessage
  │     └── PakPropAgent.process() → Gemini function-calling loop
  │           └── Tool calls → DB queries, AI orchestration
  └── Send reply via Meta API
  Return 200 to Meta (always — to prevent retry)
```

---

## 18. QA — Known Risks & Testing Surface

### High-Risk Areas

| Area | Risk | Mitigation |
|---|---|---|
| OTP brute force | 5-attempt lockout only | Rate limiter (3/hour) + lockout together |
| Duplicate WhatsApp messages | Meta retries on non-200 | 48h idempotency key in Redis |
| Legal status FSM | Illegal transitions corrupt data | `clean()` enforced + unit tests needed |
| EscrowDeal uniqueness | Two buyers locking same property | `UniqueConstraint` at DB level |
| Redis unavailability | AI cache miss, session loss | Graceful fallback: AI calls proceed without cache |
| Gemini rate limits | AI tools silently fail | `GeminiClient` returns structured error; agent notifies user |
| PDF generation memory | Large audit reports | Run in Celery worker; memory-isolated from web process |
| Tenant isolation bypass | Agent accessing other org data | Middleware + ViewSet queryset scoping; needs integration tests |

### Testing Surface

| Layer | What to Test |
|---|---|
| Models | `clean()` validators, FSM transitions, property methods |
| Views | Role-based access (403 for wrong role, 200 for correct), scoped querysets |
| WhatsApp webhook | Signature validation, idempotency, new user auto-creation |
| AI Orchestrator | Gemini response parsing, cache hit/miss behavior |
| Celery tasks | `expire_deal_locks` accuracy, notification retry logic |
| Payment webhooks | HMAC validation, status transitions on confirmed payment |
| Fraud blacklist | Redis sync on save/delete, fast-path lookup |

### Current Testing State

- Test files exist in each app (`tests.py`) but are largely skeletal
- No CI pipeline configured yet
- Priority: authentication flow, RBAC scoping, escrow uniqueness constraint

---

## 19. Deployment Model

### Environment Variables Required

```bash
SECRET_KEY=...
DEBUG=False
ALLOWED_HOSTS=yourdomain.com
DATABASE_URL=postgresql://...
REDIS_URL=redis://...

# WhatsApp
WA_VERIFY_TOKEN=...
WA_APP_SECRET=...
WA_ACCESS_TOKEN=...
WA_PHONE_NUMBER_ID=...
WA_OTP_TEMPLATE_NAME=...

# AI
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash-lite
AI_BACKEND=gemini

# Storage (optional — falls back to local disk)
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=...
R2_PUBLIC_URL=...

# Payments (optional — manual mode if omitted)
SAFEPAY_MERCHANT_KEY=...
SAFEPAY_SECRET_KEY=...
SAFEPAY_ENVIRONMENT=sandbox

# Monitoring (optional)
SENTRY_DSN=...
BASE_URL=https://yourdomain.com
```

### Process Types (Render/Railway/Fly.io)

```
web:    gunicorn config.wsgi:application --workers 2 --threads 4
worker: celery -A config.celery worker --loglevel=info
beat:   celery -A config.celery beat --loglevel=info
```

### Cost Estimate (MVP)

| Service | Plan | Monthly Cost |
|---|---|---|
| PostgreSQL (Neon/Supabase) | Free tier | $0 |
| Redis (Upstash) | Free tier (10k req/day) | $0 |
| Hosting (Render) | Starter | $7 |
| Cloudflare R2 | Free (10GB storage, 1M ops) | $0 |
| Gemini API | Free tier (60 QPM) | $0–$5 |
| Meta WhatsApp | Free (1000 conversations/month) | $0 |
| **Total** | | **~$7–$12/month** |

---

*End of Backend Architecture Documentation*
