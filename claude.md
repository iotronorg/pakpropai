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

## 12. SECURITY

- OTP authentication
- JWT tokens
- RBAC:
  - User
  - Agent
  - Developer

- Secure payments via providers only

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

- Write plan in `tasks/todo.md`
- Track progress
- Add review section

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
