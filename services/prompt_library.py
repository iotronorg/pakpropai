PROMPTS = {
    'intent_classify': """Classify the user's WhatsApp message into ONE intent.

Message: "{message}"

Allowed intents:
- property_search   (looking to buy/rent/find properties)
- tax_query         (FBR, 7E, filer status, property tax)
- verify_doc        (verify property documents/registry)
- check_loan        (loan/mortgage eligibility)
- scam_check        (verify an agent or check for fraud)
- lock_deal         (token/escrow/locking a deal)
- greeting          (hi/salam/start)
- unknown           (anything else)

Output ONLY a JSON object. No prose, no preface, no markdown fences.
Schema: {{"intent": "<one of the above>", "entities": {{}}, "confidence": 0.0}}
""",

    'property_score': """You are scoring a Pakistani property for investment risk and quality.

Property details:
- Title: {title}
- City: {city}
- Location: {location}
- Type: {property_type}
- Area: {area_marla} marla
- Price: PKR {price_pkr}
- Legal status: {legal_status}

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "score": 0-100,
  "risk": "low" | "medium" | "high",
  "factors": ["short reason 1", "short reason 2"],
  "suggestion": "one-line investor recommendation"
}}
""",

    'tax_7e_advisor': """You are a Pakistani tax advisor specializing in Section 7E (Capital Value Tax on immovable property).

Inputs:
- Property fair market value: PKR {fmv}
- Filer status: {filer_status}
- Number of properties owned: {properties_count}

Apply current FBR rules. Section 7E imposes 1% on FMV exceeding PKR 25 million per property; one self-occupied house is exempt.

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "tax_liability_pkr": <integer>,
  "exempt": <bool>,
  "exemption_reason": "<string or empty>",
  "advice": "<2 sentences max>"
}}
""",

    'loan_eligibility': """Assess Pakistani home loan eligibility.

Inputs:
- Monthly income: PKR {monthly_income}
- Loan amount needed: PKR {loan_amount}
- Tenure (years): {tenure_years}
- Existing obligations (PKR/month): {existing_emi}

Use these rules of thumb:
- Most banks cap EMI at 50% of net income
- Indicative rate: 14% per annum
- Minimum income: PKR 50,000

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "eligible": <bool>,
  "estimated_monthly_emi_pkr": <integer>,
  "max_loan_pkr": <integer>,
  "reason": "<one-line>",
  "next_steps": ["<step 1>", "<step 2>"]
}}
""",

    'fraud_check': """You are a Pakistani real estate fraud detection assistant.

Input: "{query}"

Check for red flags such as:
- Unregistered/blacklisted agents
- Disputed registries
- Common scam patterns (fake plot files, double sale, etc.)

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "risk": "low" | "medium" | "high",
  "flags": ["flag 1", "flag 2"],
  "recommendation": "<one paragraph max>",
  "verify_steps": ["actionable step 1", "actionable step 2"]
}}
""",

    'ocr_property_doc': """You are extracting structured data from a Pakistani property document image.

Extract the following fields if present:
- registry_number
- owner_name
- cnic
- property_address
- area_marla
- property_type
- mutation_date
- registrar_office

If a field isn't visible, use null. Also flag anything that looks tampered or inconsistent.

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "fields": {{}},
  "tamper_flags": [],
  "overall_confidence": 0.0
}}
""",
}


def render(template_name: str, **kwargs) -> str:
    return PROMPTS[template_name].format(**kwargs)