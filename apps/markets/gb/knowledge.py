"""
UK-specific real estate knowledge block.
Injected into the AI system prompt for GB-country organizations.
"""

MARKET_KNOWLEDGE = """
═══════════════════════════════════════════════════════
UK MARKET — LISTING & SEARCH
═══════════════════════════════════════════════════════
Property listing: collect city/town → postcode area → size (sqft or bedrooms) → asking price (GBP) → property type (flat/terraced/semi-detached/detached/bungalow/land/commercial).
If city or area is missing, ask: "Which city or area are you interested in?"
If search returns live_search_pending=true, add: "Live listings from Rightmove and Zoopla are also being checked."

═══════════════════════════════════════════════════════
STAMP DUTY LAND TAX (SDLT) — England & Northern Ireland
═══════════════════════════════════════════════════════
Residential (standard rates):
- Up to £250,000: 0%
- £250,001–£925,000: 5%
- £925,001–£1,500,000: 10%
- Above £1,500,000: 12%

First-time buyer relief: 0% up to £425,000, 5% on £425,001–£625,000 (no relief above £625,000).
Second home / buy-to-let: +3% surcharge on all bands.
Note: Scotland uses LBTT; Wales uses LTT — rates differ. Advise clients to check local rules.

═══════════════════════════════════════════════════════
CAPITAL GAINS TAX (CGT) ON PROPERTY
═══════════════════════════════════════════════════════
- *Primary Residence (PPR)*: Fully exempt from CGT — no tax on sale of your only/main home.
- *Second homes / investment property*: CGT applies on gain above annual exempt amount (£3,000 for 2024/25).
- CGT rates on property: Basic rate taxpayer 18%; Higher/additional rate taxpayer 24%.
- Report and pay within *60 days* of completion on UK residential property.

═══════════════════════════════════════════════════════
LEASEHOLD vs. FREEHOLD
═══════════════════════════════════════════════════════
- *Freehold*: You own the property and the land outright. Most houses are freehold.
- *Leasehold*: You own the property for a fixed term (e.g. 125 or 999 years). Most flats are leasehold.
- Key checks for leasehold: years remaining on lease (< 80 years is problematic for mortgages), annual service charges, ground rent (ground rent > £250/yr is considered "onerous").
- *Share of Freehold*: Flat owners collectively own the freehold — preferred structure.
- Leasehold Reform Act 2024: Check latest rules on lease extensions and ground rent reforms.

═══════════════════════════════════════════════════════
UK MORTGAGE OVERVIEW
═══════════════════════════════════════════════════════
- Standard LTV: Up to 95% (5% deposit) available, best rates at 60–75% LTV
- Rates (2024): 2yr fixed ~4.5–5.5%, 5yr fixed ~4.3–5.2% (varies by lender and LTV)
- Affordability: Lenders typically cap borrowing at ~4–4.5x annual income
- Mortgage term: Up to 35 years (max age 70–75 at end of term)
- Help to Buy: Equity Loan scheme closed in March 2023 (legacy holders only)
- Shared Ownership: Buy 25–75% of property, pay rent on remainder. Suitable for first-time buyers.

═══════════════════════════════════════════════════════
PROPERTY PURCHASE PROCESS
═══════════════════════════════════════════════════════
Step 1 — OFFER ACCEPTED: Verbal/written offer accepted. No legal obligation yet (England/Wales — "subject to contract").
Step 2 — INSTRUCT SOLICITOR: Both buyer and seller appoint conveyancing solicitors.
Step 3 — SURVEYS & SEARCHES: Building survey (RICS Level 2 or 3), local authority searches, drainage searches.
Step 4 — EXCHANGE OF CONTRACTS: Legally binding. Buyer pays deposit (~10%). Completion date agreed.
Step 5 — COMPLETION: Remaining funds transferred. Keys released. SDLT paid within 14 days.
Step 6 — LAND REGISTRY: Solicitor registers new ownership at HM Land Registry (2–6 weeks).

═══════════════════════════════════════════════════════
🚨 COMMON SCAM PATTERNS IN UK
═══════════════════════════════════════════════════════
1. *Conveyancing Fraud*: Criminals intercept emails and redirect completion funds. Always verify bank details by phone with your solicitor — never trust email-only changes.
2. *Fake Rental Listings*: Scammer lists property they don't own, takes deposit. Always view in person and verify landlord owns the property.
3. *Land Banking Scams*: Selling "investment land" with false promises of planning permission. Research planning status independently via local council.
4. *Title Fraud*: Fraudsters change Land Registry details on unmortgaged properties. Register for Land Registry property alert service (free).
5. *Clone Solicitor Fraud*: Impersonating legitimate law firms. Verify solicitors on SRA register (sra.org.uk).

═══════════════════════════════════════════════════════
PRE-PURCHASE CHECKLIST (UK)
═══════════════════════════════════════════════════════
✅ Verify title at HM Land Registry (official copies of title register)
✅ Instruct RICS-registered surveyor (Level 2 or 3 survey)
✅ Check local authority searches (planning, highways, environmental)
✅ For leasehold: Check years remaining on lease, service charge accounts, ground rent
✅ Check building's EPC (Energy Performance Certificate) rating
✅ Confirm solicitor is on SRA register
✅ Never transfer funds based on email instructions alone — always call to confirm
✅ Check for Japanese knotweed and flood risk (EA flood map)
"""
