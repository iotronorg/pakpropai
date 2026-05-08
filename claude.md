# PakProp AI – System Context for Claude

You are working on a real-world production product called:

**PakProp AI – The Trust Infrastructure Layer for Pakistani Real Estate**

This document is the **ABSOLUTE SOURCE OF TRUTH**.

You MUST strictly follow this in ALL responses.  
You MUST NOT deviate, redesign core concepts, or over-engineer.

---

## 1. CORE PRODUCT DEFINITION

PakProp AI is NOT:

- A listing platform
- A CRM
- A marketplace

PakProp AI IS:  
→ A **Trust Infrastructure Layer**

**Core function:**  
Transform fragmented property data into:

- Verified
- Actionable
- Transactable intelligence

---

## 2. CORE MISSION

Solve the trust gap by:

- Verifying properties
- Qualifying buyers
- Enabling secure transactions
- Providing AI-driven decision intelligence

---

## 3. CORE PRODUCT LOOP (MANDATORY)

ALL features MUST align with:

**DISCOVER → VERIFY → DECIDE → CONNECT → TRANSACT**

**Mapping:**

- Discover → Property Search (scraped + user listings)
- Verify → Scam Check + Verification
- Decide → AI scoring + Tax + Loan
- Connect → Talk to Agent
- Transact → Deal Lock + Escrow

If a feature does NOT support this loop → it is secondary.

---

## 4. PRIMARY INTERFACE (CRITICAL)

System is:

→ **WhatsApp-First (Primary Interface)**  
→ **Web Dashboard (Secondary Interface – Phase 2)**

- NO mobile app (at any stage initially)

All core user interactions happen via:  
→ WhatsApp Cloud API

Web dashboard is used for:

- Agent operations
- Developer SaaS usage
- Admin control and monitoring

**Design must be:**

- Conversational (WhatsApp-first)
- State-aware
- Low friction
- Urdu + English friendly

---

## 5. CORE FEATURE SYSTEM (NON-NEGOTIABLE)

### A. PROPERTY DISCOVERY

- Unified property search via:
  - Scraped listings (Zameen, Graana, OLX)
  - User-submitted listings
  - Agent inventory

- Search via WhatsApp  
  Example:  
  `"5 marla plot DHA Lahore under 1 crore"`

- Filters:
  - Location
  - Price
  - Size
  - Property type
  - Verified toggle

- Property listing via:
  - Text
  - Voice
  - Image

- AI must structure all inputs into normalized property schema

---

### B. VERIFICATION & TRUST

- Property verification engine:
  - OCR (documents)
  - Cross-check listings
  - Agent/community validation

- **Scam Check (CORE VIRAL FEATURE):**
  - Input: link / screenshot / voice
  - Output: risk + reasoning + recommendation

- **Property Audit (Premium):**
  - Ownership clarity
  - Legal flags
  - Risk score
  - Market comparison
  - Output: PDF + WhatsApp summary

---

### C. DECISION ENGINE (AI CORE)

- Property Risk Score (1–10)
- Investment Grade
- Liquidity Score

- Loan Eligibility Engine:
  - Apna Ghar scheme logic

- Tax Engine:
  - 7E
  - CGT
  - Rental tax
  - Local + Overseas Pakistanis

- Infrastructure Intelligence:
  - Growth prediction
  - Nearby developments

---

### D. CONNECTION & LEADS

- **Talk to Agent:**
  - Match verified agent
  - Connect via WhatsApp

- **Lead Qualification Engine:**
  - Score based on:
    - Budget
    - Behavior
    - Intent

- **Lead Distribution:**
  - High-quality leads → agents/developers

---

### E. TRANSACTION SYSTEM

- **Deal Lock:**
  - Token payment (PKR 25K–100K)
  - 48-hour exclusivity

- **Escrow:**
  - Safepay / bSecure integration

- System MUST NOT hold funds

---

### F. DATA INPUT SYSTEM

- **Document Upload (CRITICAL):**
  - Allotment letters
  - Government notifications
  - Tax documents
  - Society records

- Purpose:
  - Train AI
  - Improve verification
  - Improve tax accuracy

- **Voice-to-Data:**
  - Convert agent voice into structured listings

---

### G. DASHBOARD (LATER PHASE)

- Agent dashboard:
  - Listings
  - Leads
  - Conversions

- Developer dashboard:
  - Inventory
  - Lead analytics

- Admin dashboard:
  - Moderation
  - Fraud monitoring

---

## 6. ARCHITECTURE PRINCIPLES

1. **MVP FIRST (CRITICAL)**
   - Build FAST
   - Avoid over-engineering

2. **MODULAR MONOLITH FIRST**
   - Django
   - Single deployable
   - Internal modular apps

3. **MICROSERVICES LATER**
   - Extract only when needed

4. **COST-AWARE DESIGN**
   - Prefer free tools
   - Optimize for $0–$20/month

---

## 7. TECH STACK RULES

**Backend:**

- Django (MANDATORY)
- Django Rest Framework

**Async:**

- Celery + Redis (Upstash free tier)

**Database:**

- PostgreSQL (Supabase / Neon)

**AI:**

- Gemini API (free tier)

**Storage:**

- Cloudflare R2 / Supabase Storage

**Hosting:**

- Render / Railway / Fly.io

**Messaging:**

- WhatsApp Cloud API

---

## 8. SYSTEM PHASES

### PHASE 1 (MVP)

- WhatsApp bot
- Scam Check
- Basic property input
- Simple AI responses
- Lead capture

### PHASE 2

- Property scoring
- Verification improvements
- Loan + tax engines

### PHASE 3

- Microservices
- Event-driven system
- Escrow
- Dashboards

---

## 9. DATA PHILOSOPHY

Data is the CORE ASSET.

System must:

- Store structured property data
- Store user interactions
- Improve AI continuously

---

## 10. AI DESIGN RULES

AI is CORE.

Must include:

- Prompt design
- Cost optimization
- Caching

Must support:

- OCR
- Voice
- Urdu + English

---

## 11. WHATSAPP DESIGN RULES

- Conversational flows
- Session state management
- Multi-step interactions

**DO NOT:**

- Build UI-heavy systems
- Overcomplicate flows

---

## 12. SECURITY & USER ROLES

- OTP authentication
- JWT tokens
- Secure payments via providers only

### RBAC — 4 User Roles

**1. Client (role=`user`)**
- Interacts exclusively via WhatsApp (primary); future: mobile app, web portal, phone AI, social DMs
- No web dashboard access — NEVER expose internal admin/CRM UI to clients
- Gets auto-created as CRM Lead on first WhatsApp message
- Can search, verify, get tax/loan advice, connect to agents, submit listings, lock deals
- See Section 17 for full Client Role specification

**2. Agent (role=`agent`)**
- Has an `Agent` profile linked via `User.agent_profile` (OneToOneField)
- Can belong to a Developer org via `Agent.parent_organization`
- Types: Independent Agent, Developer Sales Agent, Agency Sales Agent
- Web dashboard: manages own listings, views assigned leads only, accesses WhatsApp CRM
- API: `GET /agents/me/` returns their agent profile
- Lead ViewSet: returns only `lead.assigned_agent == request.user.agent_profile`
- See Section 18 for full Agent Role specification

**3. Developer / Agency (role=`developer`)**
- An ORGANIZATION-LEVEL entity (housing societies, builders, agencies, brokerage firms)
- Also represented as an `Agent` record (`agent_type=agency/developer`)
- Web dashboard: full inventory management, org-wide lead visibility, team management, sales analytics
- Sees ALL organization leads (NOT scoped to single agent — key difference from Agent role)
- Strict org-level data isolation: can never access another org's data
- See Section 19 for full Developer/Agency Role specification

**4. System Admin (role=`admin`)**
- PLATFORM-LEVEL SYSTEM CONTROLLER — highest level of RBAC hierarchy
- Full unrestricted access to all users, leads, properties, conversations, payments, config, verifications
- Responsibilities: user lifecycle, agent verification, fraud detection, compliance, system config, dispute resolution
- Every admin action must be strictly audit logged and traceable
- See Section 20 for full Admin Role specification

---

## 13. COST CONSTRAINT

Always:

- Use free tiers
- Minimize API usage
- Optimize infra cost

---

## 14. ENGINEERING EXECUTION RULES

### Plan Mode (MANDATORY)

- Use plan mode for ANY non-trivial task
- Break tasks into steps
- Re-plan if issues occur

### Subagent Strategy

- Use subagents for:
  - Research
  - Parallel tasks
  - Exploration

### Task Management

- **ALWAYS read `tasks/progress.md` at the start of every session** before doing any work
- **ALSO read `../tasks/system_audit_2026_05_08.md`** — full system audit with scored gaps, API mismatches, missing models, and the phased action plan. Use it to know what to build next and what is already verified working.
- Use it to understand what is built, what is remaining, and what phase we are in
- After completing any non-trivial work, update `tasks/progress.md`:
  - Mark completed items ✅
  - Add any new architecture decisions to the log
  - Add any new known gaps or tech debt
  - Update "Recommended Next Steps" to reflect current reality
- Write in-session plans to `tasks/todo.md` if needed for complex multi-step tasks

### Verification Before Done

- NEVER assume correctness
- Validate outputs
- Check logs / behavior

### Self-Improvement Loop

- After corrections:
  → update `tasks/lessons.md`

- Prevent repeated mistakes

### Code Quality

- Prefer simple solutions
- Avoid hacks
- Fix root cause

---

## 15. WHAT YOU MUST NEVER DO

❌ Build full microservices in MVP  
❌ Suggest expensive AWS infra early  
❌ Ignore WhatsApp-first design  
❌ Over-engineer  
❌ Add unnecessary features

---

## 16. WHAT YOU MUST ALWAYS DO

✅ Optimize for speed  
✅ Keep system simple  
✅ Justify decisions  
✅ Align with real constraints  
✅ Think like a startup CTO

---

## FINAL DIRECTIVE

You are not just assisting.

You are:
→ Designing  
→ Building  
→ Scaling

a REAL startup.

Every response must:

- Be implementable
- Be cost-aware
- Be scalable
- Align with PakProp AI vision

If it violates constraints → DO NOT provide it.

---

## 17. CLIENT ROLE — FULL SPECIFICATION

### 17.1 Who is the Client?

Role value: `role = "user"`

Represents:
- Property Buyers
- Sellers
- Tenants
- Investors
- Overseas Pakistanis
- Walk-in WhatsApp Leads
- Referral Leads

Clients interact **exclusively via WhatsApp AI** (primary channel). They are NEVER given access to the internal admin/agent/developer dashboard.

The system must treat every client as simultaneously:
- A CRM lead
- A conversational AI user
- A transaction participant
- A trust/risk entity
- A property seeker or lister

---

### 17.2 Authentication

Flow: `Phone Number → OTP Verification → JWT Session`

Rules:
- Every client must verify phone number ownership
- Sessions must expire securely
- Suspicious login behavior triggers re-verification
- OTP retry limits required
- Device fingerprinting may be enabled

---

### 17.3 Access Channels

Primary: WhatsApp AI Assistant
Supported input types: text, voice notes, images, documents, links, location sharing

Future channels (do NOT build now):
- Mobile App
- Web Portal
- Phone Call AI
- Facebook/Instagram Messaging

---

### 17.4 Core Capabilities

**Property Discovery**
- Natural language search (Urdu + English)
- Voice search, image/screenshot search
- Filters: city, society, price, area, type, furnished, installments, intent
- Example: `"5 marla plot DHA Lahore under 1 crore"`

**Property Recommendations**
- AI-generated recommendations
- New listing alerts, price drop alerts, matching alerts

**Scam & Trust Verification**
- Submit suspicious links, screenshots, listings
- Request seller/agent verification
- Receive AI-generated trust/risk score

**Property Listing (AI-guided)**
- List via chat, voice, or photo
- AI extracts: property type, location, dimensions, price, urgency, ownership type
- AI converts conversation into structured listing

**Document Verification**
- Upload property documents
- OCR extraction + AI validation
- Ownership consistency checks, tampering detection

**Legal & Tax Guidance**
- Property tax, 7E tax, CGT, rental income tax
- Overseas remittance/property questions
- Registry and transfer guidance

**Loan & Financing Assistance**
- Loan eligibility checks
- Apna Ghar scheme guidance
- EMI and down payment estimation

**Property Audit (Premium)**
- AI audit: ownership clarity, legal flags, risk score, market comparison
- Output: PDF report + WhatsApp summary

**Agent Connection**
- AI matches and connects to suitable agent via WhatsApp

**Visit Scheduling**
- Schedule property visits, virtual tours, reminders, rescheduling

**Deal Lock / Escrow**
- Initiate deal lock flow (PKR 25K–100K token payment, 48-hour exclusivity)
- Track own deal locks: `GET /escrow/mine/`

---

### 17.5 AI Behavior for Clients

On first WhatsApp message:
- Auto-create CRM Lead record
- Store all conversation history

AI continuously tracks and updates:
- preferences, budget, city, buying intent, urgency
- investment profile, property search history
- lead score, engagement score, trust score, conversion probability

AI may:
- proactively recommend properties
- trigger follow-up reminders
- escalate hot leads to agents
- detect scam risk and fake buyers/spam

---

### 17.6 Client Assignment Logic

Clients may be assigned to individual agents, agencies, or developer sales teams.

Assignment methods:
- Manual (admin)
- AI-assisted (intent/budget match)
- Rule-based (geo, budget thresholds)

---

### 17.7 Security Constraints

Clients MUST NOT access:
- Admin systems or CRM dashboards
- Internal analytics
- Other users' data
- Platform settings
- Internal notes

Clients can ONLY access:
- Their own conversations
- Their own documents
- Their own deal locks
- Their own submitted properties

---

### 17.8 Trust & Compliance

All client activity must be logged for:
- Fraud detection and audit trails
- Dispute resolution
- Escrow compliance
- AML/risk monitoring

System must support:
- Suspicious activity detection
- Duplicate lead detection
- Fake listing detection
- Spam and WhatsApp abuse prevention

---

### 17.9 Notifications to Clients

Via WhatsApp only (for now):
- Property recommendations
- Visit reminders
- Deal and escrow updates
- Verification results
- Market insights and price alerts

---

### 17.10 Future Expansions (Do NOT Build Now)

- AI investment advisor
- AI negotiation assistant
- Digital property wallet
- Cross-border investment flows
- Blockchain title verification
- E-signatures + automated sale agreements
- AI-based property valuation
- Smart escrow system

---

## 18. AGENT ROLE — FULL SPECIFICATION

### 18.1 Who is the Agent?

Role value: `role = "agent"`

The Agent is a sales and relationship management entity responsible for handling leads, managing property listings, coordinating buyers/sellers, and closing transactions.

Agent types:
- **Independent Agent** — works individually, manages personal inventory and self-generated/assigned leads
- **Developer Sales Agent** — works under a developer org, sells developer inventory, uses developer-approved workflows
- **Agency Sales Agent** — works under a real estate agency, collaborates with agency managers, follows agency-level permissions

Each Agent has: Agent Profile, Verification Status, KYC Information, Performance Metrics, Assigned Leads, Listing Portfolio.

Data model relationships:
- Linked via `User.agent_profile` (OneToOneField)
- Optional parent org via `Agent.parent_organization`

---

### 18.2 Authentication

Flow: `POST /auth/otp/send/ → OTP → JWT Session`

Rules:
- OTP-based auth required
- JWT session expiration mandatory
- Device/session tracking supported
- Suspicious login detection required
- Activity logging mandatory
- Optional 2FA support

---

### 18.3 Access Channels

Primary: Web Dashboard

Integrated: WhatsApp CRM, AI Lead Assistant, Notifications System

Future (do NOT build now): Mobile Agent App, Call Center Interface, AI Voice Assistant

---

### 18.4 Core Capabilities

**Profile Management**
- View/update own profile, upload KYC documents, manage availability status
- `GET /agents/me/`

**Lead Management**
- View assigned leads only (`lead.assigned_agent == request.user.agent_profile`)
- Track lifecycle, update status, add internal notes, schedule callbacks, manage pipeline
- `GET /leads/` (scoped) | `PATCH /leads/<id>/`
- Statuses: New → Contacted → Interested → Visit Scheduled → Negotiating → Closed Won / Closed Lost / Spam / Unresponsive

**CRM & Communication**
- Access WhatsApp CRM conversations for assigned leads
- Reply to leads, send follow-ups, view communication history
- AI assists with: lead prioritization, smart follow-ups, response drafting, sentiment detection, conversion prediction

**Property Listing Management**
- View/create/edit/archive own listings, upload images/videos/documents, manage availability
- `GET /properties/` (scoped) | `POST /properties/`
- Future: `PATCH /properties/<id>/` | `DELETE /properties/<id>/`

**Property Verification**
- Submit ownership/seller documents, request verification, track status
- `POST /verification/`

**Document Access**
- View OCR results and verification findings for own properties
- `GET /verification/documents/?verification=<id>`

**Property Audit Reports**
- Download audit PDFs, share with clients, review AI property analysis
- `GET /audit/download/<id>/`

**Visit & Appointment Management**
- Schedule/confirm/reschedule property visits, manage calendar, receive reminders
- Future: `POST /appointments/` | `PATCH /appointments/<id>/`

**Deal & Transaction Assistance**
- Assist deal lock process, coordinate token payments, track negotiations
- Agents CANNOT: release escrow funds, override compliance checks, approve legal verification

---

### 18.5 Data Access Scope

Agents can ONLY access:
- **Leads:** assigned leads only
- **Listings:** own listings (+ org listings if permitted)
- **Conversations:** own assigned CRM conversations
- **Analytics:** personal performance metrics; limited org metrics
- **Documents:** documents linked to owned/assigned properties

---

### 18.6 Access Restrictions

Agents MUST NOT:
- Access platform-wide analytics
- Access other agents' private leads
- Access admin systems or modify RBAC settings
- View financial compliance or escrow control systems
- Access super admin tools

---

### 18.7 AI Behavior for Agents

AI should assist agents by:
- Recommending next actions on leads
- Detecting hot leads and triggering alerts
- Auto-summarizing conversations
- Suggesting follow-up sequences
- Detecting suspicious/fraudulent activity
- Recommending similar properties to match buyer interest

AI may auto-generate: listing drafts, WhatsApp reply suggestions, follow-up sequences, lead summaries.

AI assists but NEVER overrides compliance or RBAC constraints.

---

### 18.8 Performance & Analytics

Agent metrics: response time, lead conversion rate, active listings, deal closures, client satisfaction, verification success rate, visit completion rate.

All analytics scoped to the agent's authorized visibility only.

---

### 18.9 Compliance & Audit Logging

All agent activity must be logged:
- Lead status changes and notes
- Listing edits and document uploads
- Verification requests
- Conversation actions

Supports: audit trails, fraud detection, abuse prevention, compliance monitoring.

---

### 18.10 Organization Hierarchy

Agents may belong to agencies, developers, or internal sales teams.

Organization hierarchy may define:
- Lead routing rules
- Listing visibility scope
- Reporting structure
- Approval workflows

---

### 18.11 Future Expansions (Do NOT Build Now)

- AI sales assistant
- Commission tracking
- Automated follow-up campaigns
- Voice calling integration
- Territory management + smart lead routing
- AI negotiation assistant
- E-signature workflows
- Sales forecasting

---

## 19. DEVELOPER / AGENCY ROLE — FULL SPECIFICATION

### 19.1 What is the Developer/Agency?

Role value: `role = "developer"`
Internal representation: `Agent` record with `agent_type = "developer" | "agency"`

This is an **ORGANIZATION-LEVEL ENTITY**, not an individual user.

Can represent:
- Housing societies
- Builders and construction companies
- Real estate developers
- Real estate agencies
- Brokerage firms

A Developer operates at the **STRATEGIC + ADMIN** level. Agents under it operate at the **EXECUTION** level.

---

### 19.2 Organization Structure

A Developer org can:
- Own multiple real estate projects
- Manage multiple agents and teams across cities
- Handle bulk property inventory
- Run sales operations at scale

Internal hierarchy:
- Organization Owner/Admin
- Sales Managers → Team Leads → Agents (linked users) → CRM Operators

---

### 19.3 Authentication

Same flow as Agent: `OTP → JWT`

---

### 19.4 Core Capabilities

**Lead Management (org-wide, NOT scoped)**
- View ALL leads across the organization — not restricted to a single agent's leads
- `GET /leads/` | `PATCH /leads/<id>/`
- Includes: conversion tracking, pipeline visibility, source tracking, funnel analytics
- `GET /leads/?filters=`

**Inventory Management**
- View, create, edit all organization properties
- `GET /properties/` | `POST /properties/` | `PATCH /properties/<id>/`

**Team Management**
- View all agent profiles within organization, monitor agent activity and performance
- `GET /agents/`

**Analytics Dashboard**
- Org-wide sales analytics, lead funnel, conversion tracking, source performance
- Team performance metrics, CRM pipeline monitoring

---

### 19.5 Data Access Scope

Developers can access (all scoped to their own organization only):
- All organization leads
- All agent data within organization
- All organization property listings
- Organization-level analytics and CRM pipeline data
- Team performance metrics

**Strict isolation rule:** Developers can NEVER access another organization's data. All API queries must be filtered by `organization == request.user.developer_org`.

---

### 19.6 Key Difference from Agent Role

| | Agent | Developer/Agency |
|---|---|---|
| Lead visibility | Assigned leads only | All org leads |
| Listing scope | Own listings | All org listings |
| Analytics | Personal metrics | Org-wide metrics |
| Team management | No | Yes |
| Operation level | Execution | Strategic + Admin |

---

### 19.7 AI Assistance for Developers

AI should assist with:
- Sales forecasting and conversion optimization
- Lead distribution insights (which agent gets which lead)
- Inventory demand prediction
- Team performance analysis
- Marketing effectiveness insights

AI can surface:
- High-performing agents
- Hot leads needing attention
- Underperforming listings
- Pricing insights

---

### 19.8 System Behavior Rules

- Strict org-level data isolation — no cross-org access under any circumstance
- All developer actions must be audit logged
- CRM and inventory data consistency must be maintained
- AI assists analytics and routing but does not override admin decisions

---

### 19.9 Architectural Rule

**Developer role = ORGANIZATION CONTROL LAYER**

Responsible for: teams, inventory, leads, analytics, revenue tracking.

NOT responsible for: individual sales execution (that is the Agent's domain).

---

## 20. SYSTEM ADMIN ROLE — FULL SPECIFICATION

### 20.1 What is the Admin?

Role value: `role = "admin"`

The Admin is the **PLATFORM-LEVEL SYSTEM CONTROLLER** — the highest level of the RBAC hierarchy — responsible for governance, compliance, security, fraud prevention, and full system oversight. Admins are internal operators.

RBAC hierarchy:
```
System Admin
    └── Developers / Agencies (organization level)
            └── Agents / Sales Consultants
                    └── Clients / Leads
```

---

### 20.2 Core Responsibilities

- System configuration and runtime setup
- Full user lifecycle management (create, suspend, ban)
- Agent verification and approval
- Fraud detection and prevention
- Compliance enforcement and dispute resolution
- Platform-wide monitoring and security oversight
- System analytics and reporting
- Role and permission governance
- AI workflow configuration
- Payment and billing control

---

### 20.3 Full API Access (No Scoping)

**User Management**
- `GET /auth/users/` | `PATCH /auth/users/<id>/`
- Edit compliance fields: CNIC, NTN, tax filer status

**Agent Management**
- `GET /agents/` | `POST /agents/` | `PATCH /agents/<id>/` | `DELETE /agents/<id>/`
- Toggle verification, status, featured flag

**Property Management**
- `GET /properties/` | `POST /properties/` | `PATCH /properties/<id>/` | `DELETE /properties/<id>/`
- Verify, rescore, assign owner + agent on any property

**Lead Management**
- `GET /leads/` (fully unscoped) | `PATCH /leads/<id>/`

**Verification System**
- `GET /verification/queue/` | `PATCH /verification/queue/<id>/` — approve/reject
- `GET /verification/documents/`
- `POST /verification/documents/<scan>/link/<verification_id>/`

**Fraud Monitoring**
- `GET /verification/fraud/stats/` — analytics dashboard
- `GET /verification/fraud/alerts/` — live alerts feed
- `GET /verification/fraud/users/` — flagged users
- `GET|POST|DELETE /verification/fraud/blacklist/` — blacklist management

**Payments & Billing**
- `GET /payments/` | `POST /payments/checkout/<deal_id>/`

**Deal Lock / Escrow**
- `GET /escrow/` — system-wide view
- `POST /escrow/lock/<id>/cancel/` — cancel any lock

**System Configuration (CRITICAL)**
- `GET /config/` | `PATCH /config/`
- Controls: feature flags (OCR, voice, scrapers, payments), API keys, system toggles, integrations, AI behavior

**Audit & Compliance**
- `GET /audit/download/<id>/` — any audit PDF, full system audit visibility

---

### 20.4 Data Access Scope

Admins have **UNRESTRICTED** access to:
- All users, agents, developers/agencies
- All leads, properties, CRM/WhatsApp conversations
- All verification records and fraud data
- All payments and escrow
- All system configuration and analytics

Admins override all RBAC restrictions. No data is hidden from admin.

---

### 20.5 System Behavior Rules

- Full audit logging on EVERY admin action — no exceptions
- Sensitive actions (delete, suspend, blacklist) must be individually traceable
- Admin operations must be reversible where possible
- AI suggestions must NOT bypass compliance rules
- No hidden or restricted data from admin role

---

### 20.6 AI Behavior for Admins

AI should assist with:
- Fraud detection alerts and anomaly detection
- System health monitoring and risk scoring
- Suspicious behavior detection
- Conversion and revenue analytics
- Platform activity summaries

AI must:
- Highlight critical system issues proactively
- Prioritize fraud signals in the admin view
- Suggest preventive actions

AI must NOT override compliance rules even when assisting admins.

---

### 20.7 Future Internal Role Extensions (Do NOT Build Now)

Future roles will follow hierarchical RBAC inheritance under Admin:
- `super_admin`, `finance_manager`, `legal_officer`
- `marketing_manager`, `support_agent`, `field_officer`
- `verification_officer`, `call_center_agent`, `regional_manager`

---

### 20.8 Architectural Rule

**Admin = Full System Control Layer**

- No data restrictions (except audit safety logging)
- Every action logged and traceable
- Admin is the final authority on all platform operations
