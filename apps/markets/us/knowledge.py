"""
US-specific real estate knowledge block.
Injected into the AI system prompt for US-country organizations.
"""

MARKET_KNOWLEDGE = """
═══════════════════════════════════════════════════════
US MARKET — LISTING & SEARCH
═══════════════════════════════════════════════════════
Property listing: collect city/state → neighborhood → size (sqft and bedrooms/bathrooms) → asking price (USD) → property type (single-family/condo/townhouse/multi-family/land/commercial).
If location is missing, ask: "Which city and state are you interested in?"
If search returns live_search_pending=true, add: "Additional listings from Zillow and Realtor.com are also being checked."

═══════════════════════════════════════════════════════
US TAXATION — OVERVIEW
═══════════════════════════════════════════════════════
Note: US real estate taxes vary significantly by state and county. Always recommend consulting a local CPA or tax attorney.

Key taxes:
- *Annual Property Tax*: Levied by county/municipality. Typically 0.5–2.5% of assessed value per year (varies widely — TX ~1.8%, CA ~0.75%, NJ ~2.2%).
- *Capital Gains Tax on Sale*: Federal rates apply on profit above cost basis.
  - Primary residence exclusion: Up to $250,000 gain excluded ($500,000 for married filing jointly) if lived in home 2 of last 5 years.
  - Investment property: Long-term gains (held > 1 yr) taxed at 0%, 15%, or 20% federal + state taxes.
  - Depreciation recapture: 25% federal rate on prior depreciation claimed on rental properties.
- *Transfer Tax*: State/county-specific. Ranges from 0% (TX, AK) to ~4% (VT, DE). Check local rate.
- *Mortgage Interest Deduction*: Deductible on federal taxes for primary/secondary residence (up to $750,000 loan balance).

═══════════════════════════════════════════════════════
US MORTGAGE OVERVIEW
═══════════════════════════════════════════════════════
- Standard: 30-year or 15-year fixed rate. 2024 rates ~6.5–7.5% (30yr fixed).
- Conventional: 20% down avoids PMI (Private Mortgage Insurance). As low as 3% down with PMI.
- FHA Loan: 3.5% down minimum, government-backed, credit score ≥ 580.
- VA Loan: 0% down for eligible veterans/active military.
- Jumbo Loan: For loans above conforming limits (~$766,550 in most areas). Stricter requirements.
- Affordability rule of thumb: Monthly housing costs (PITI) should not exceed 28% of gross monthly income.

═══════════════════════════════════════════════════════
PROPERTY PURCHASE PROCESS
═══════════════════════════════════════════════════════
Step 1 — OFFER: Written offer submitted through buyer's agent. Seller may accept, reject, or counter.
Step 2 — UNDER CONTRACT: Both parties sign Purchase Agreement. Earnest money deposit (1–3%) held in escrow.
Step 3 — DUE DILIGENCE: Home inspection (7–14 days), appraisal (lender-ordered), title search.
Step 4 — CLEAR TO CLOSE: Lender issues final approval. Final walkthrough.
Step 5 — CLOSING: Sign documents, pay closing costs (2–5% of purchase price). Title transfers. Keys released.
Closing costs include: Lender fees, title insurance, escrow fees, prepaid taxes/insurance, recording fees.

═══════════════════════════════════════════════════════
🚨 COMMON SCAM PATTERNS IN US
═══════════════════════════════════════════════════════
1. *Wire Fraud*: Scammers intercept closing emails and redirect wire transfers. ALWAYS verify wiring instructions by phone with title company — never rely on email alone.
2. *Foreclosure Relief Scams*: Companies charging upfront fees to save homes from foreclosure. Contact HUD-approved counselors (free) instead.
3. *Fake Rental Listings*: Stolen photos, fake landlords, advance deposit theft. Always view property in person and verify landlord identity.
4. *Title Fraud*: Filing forged deeds. Protect with owner's title insurance policy (one-time fee at closing).
5. *Deed Theft (Vacant Property)*: Fraudsters file forged deeds on vacant/investment properties. Monitor your title with county recorder alerts.

═══════════════════════════════════════════════════════
PRE-PURCHASE CHECKLIST (US)
═══════════════════════════════════════════════════════
✅ Order home inspection by licensed inspector (ASHI/InterNACHI certified)
✅ Review title report for liens, encumbrances, easements
✅ Verify HOA documents and fees (if applicable)
✅ Check local flood zone (FEMA Flood Map Service)
✅ Review seller's disclosure statement for known defects
✅ Get lender appraisal to confirm purchase price vs. market value
✅ Purchase owner's title insurance policy at closing
✅ Verify wiring instructions by phone with title/escrow company
✅ Consult local CPA for state-specific tax implications
"""
