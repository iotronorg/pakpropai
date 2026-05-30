"""
UAE-specific real estate knowledge block.
Injected into the AI system prompt for AE-country organizations.
"""

MARKET_KNOWLEDGE = """
═══════════════════════════════════════════════════════
UAE MARKET — LISTING & SEARCH
═══════════════════════════════════════════════════════
Property listing: collect emirate/city (Dubai/Abu Dhabi/Sharjah) → community/area → size (sqft) → price (AED) → property type (apartment/villa/townhouse/plot/commercial).
If city is missing, ask: "Which emirate or community are you interested in?"
If search returns live_search_pending=true, add: "Live listings from Bayut and Propertyfinder are also being checked — I'll update you shortly."

═══════════════════════════════════════════════════════
UAE TAXATION — NO INCOME OR CAPITAL GAINS TAX
═══════════════════════════════════════════════════════
UAE has *no personal income tax* and *no capital gains tax* on property.
Key transaction costs:
- *DLD Transfer Fee*: 4% of property value (typically split 2% buyer / 2% seller, or as agreed)
- *DLD Admin Fee*: AED 580 (apartments/offices) | AED 430 (land) flat
- *Agency commission*: Typically 2% of transaction value (buyer's agent)
- *Mortgage registration fee* (if financed): 0.25% of loan amount + AED 290

═══════════════════════════════════════════════════════
UAE MORTGAGE RULES
═══════════════════════════════════════════════════════
Central Bank UAE mortgage caps:
- Residents, first property: Max 80% LTV (20% down payment minimum)
- Residents, second property: Max 75% LTV
- Non-residents: Max 50% LTV (50% down payment required)
- Off-plan properties: Max 50% LTV

Typical mortgage terms:
- Rates: ~4–5% p.a. fixed (5yr) or floating (EIBOR-based)
- Maximum term: 25 years (must complete before age 70)
- Monthly repayments cannot exceed 50% of monthly net income (UAE Central Bank rule)

═══════════════════════════════════════════════════════
RERA & OFF-PLAN RULES
═══════════════════════════════════════════════════════
- *RERA* (Real Estate Regulatory Agency) regulates all Dubai real estate. Verify agent RERA number at dubailand.gov.ae.
- *Oqood*: Off-plan property interim registration with DLD. Protects buyer if developer defaults.
- Escrow accounts: All off-plan payment plans must use DLD-approved escrow accounts (mandatory by law).
- *RERA escrow trustee*: Developer can only access funds as construction milestones are reached.
- Check project registration on Dubai REST app or RERA's Ejari portal.

═══════════════════════════════════════════════════════
FREEHOLD AREAS FOR EXPATS (DUBAI)
═══════════════════════════════════════════════════════
Expats can purchase freehold in designated areas: Dubai Marina, Downtown Dubai, Palm Jumeirah, JBR, Jumeirah Lakes Towers, Business Bay, Arabian Ranches, Dubai Hills, MBR City, Creek Harbour, and many others.
Abu Dhabi freehold areas: Al Reem Island, Yas Island, Al Maryah Island, Saadiyat Island.
Non-designated areas are leasehold (99-year lease) for expats.

═══════════════════════════════════════════════════════
SERVICE CHARGES (ANNUAL)
═══════════════════════════════════════════════════════
Annual service charges apply to all units in managed communities:
- Dubai: Typically AED 10–30 per sqft per year (varies by community)
- Check RERA's Service Charge Index for your community before purchase
- Service charges are mandatory and collected by building management

═══════════════════════════════════════════════════════
🚨 COMMON SCAM PATTERNS IN UAE
═══════════════════════════════════════════════════════
1. *Unregistered Agents*: Always verify RERA registration number. Unregistered agents are illegal.
2. *Fake Listings*: Photos/prices used as bait, property shown is different. Verify via Dubai REST app.
3. *Advance Payment Fraud*: Demanding cash before signing SPA or outside escrow. Always pay through escrow.
4. *Title Deed Forgery*: Request fresh title deed extract from DLD — never accept photocopies.
5. *Off-Plan Developer Fraud*: Developers without escrow account approval. Verify on RERA's approved developer list.
6. *Fake Rental Cheques*: Post-dated cheques that bounce. Verify funds or use bank guarantee.

═══════════════════════════════════════════════════════
PRE-PURCHASE CHECKLIST (UAE)
═══════════════════════════════════════════════════════
✅ Verify agent RERA registration number
✅ Title deed extract from Dubai Land Department (fresh, not photocopy)
✅ No mortgage/charge on property (check with DLD)
✅ Confirm service charge arrears are clear
✅ For off-plan: Verify Oqood registration and escrow account
✅ For secondary market: Verify NOC from developer/building management
✅ Engage a registered UAE property lawyer for final review
✅ Ensure SPA (Sales Purchase Agreement) is reviewed before signing
"""
