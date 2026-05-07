"""
Tool functions available to the PakProp AI agent.
All functions must: have type hints, a clear docstring, return JSON-serializable dicts.
The google.genai SDK auto-generates tool schemas from signatures + docstrings.
"""
import logging
import contextvars

logger = logging.getLogger(__name__)

# Thread-safe context for the current user/phone (set by agent before tool calls)
_ctx_user = contextvars.ContextVar('agent_user', default=None)
_ctx_phone = contextvars.ContextVar('agent_phone', default=None)


def set_context(user, phone: str):
    _ctx_user.set(user)
    _ctx_phone.set(phone)


# ─── Property Search ──────────────────────────────────────────────────────────

def search_properties(
    city: str,
    property_type: str = '',
    area_marla: float = 0.0,
    max_price_pkr: int = 0,
    location: str = '',
    furnished: str = '',
    construction_status: str = '',
) -> dict:
    """
    Search for available property listings in Pakistan (both database and live scraped results).
    Call this whenever the user wants to find, buy, or rent a property.

    Args:
        city: City name e.g. 'Lahore', 'Karachi', 'Islamabad', 'Rawalpindi'
        property_type: One of 'plot', 'residential', 'commercial'. Leave blank for all types.
        area_marla: Property size in Marla. 1 Kanal = 20 Marla. Use 0 if not specified.
        max_price_pkr: Maximum budget in Pakistani Rupees. Use 0 if not specified.
        location: Specific area/society e.g. 'DHA Phase 6', 'Gulberg 3', 'Bahria Town Block D'
        furnished: One of 'furnished', 'semi_furnished', 'unfurnished'. Leave blank if not specified.
        construction_status: One of 'builder', 'ready', 'under_construction'. Leave blank if not specified.
    """
    try:
        from apps.properties.search import PropertySearchService
        results = PropertySearchService.search(
            city=city,
            location=location,
            area_marla=area_marla if area_marla > 0 else None,
            max_price=max_price_pkr if max_price_pkr > 0 else None,
            property_type=property_type,
            furnished_status=furnished,
            construction_status=construction_status,
        )
        if not results:
            return {
                'count': 0,
                'message': 'No properties found matching your criteria.',
                'properties': [],
            }
        return {
            'count': len(results),
            'properties': [
                {
                    'id': r.source_id,
                    'title': r.title,
                    'city': r.city,
                    'location': r.location,
                    'area_marla': str(r.area_marla) if r.area_marla else 'N/A',
                    'price_pkr': r.price_pkr or 0,
                    'price_formatted': f"PKR {r.price_pkr:,}" if r.price_pkr else 'Price not listed',
                    'property_type': r.property_type,
                    'source': r.source,
                    'url': r.url or '',
                    'ai_verdict': getattr(r, 'ai_verdict', ''),
                }
                for r in results[:5]
            ],
        }
    except Exception as exc:
        logger.error(f"search_properties tool failed: {exc}")
        return {'count': 0, 'error': 'Search temporarily unavailable.', 'properties': []}


# ─── Tax Calculator ───────────────────────────────────────────────────────────

def calculate_7e_tax(
    fmv_pkr: int,
    filer_status: str,
    properties_count: int = 1,
    is_self_occupied: bool = False,
) -> dict:
    """
    Calculate Section 7E Capital Value Tax and other applicable property taxes in Pakistan.
    Call this when user asks about annual property tax, Section 7E, FBR tax on property,
    filer vs non-filer rates, or how much tax they owe on their property.

    Args:
        fmv_pkr: Fair Market Value of the property in Pakistani Rupees (e.g. 30000000 for PKR 3 crore)
        filer_status: Must be 'filer' or 'non_filer'. Ask user if unknown.
        properties_count: Total number of properties the person owns (default 1)
        is_self_occupied: True if this is the person's primary/only home they live in
    """
    try:
        THRESHOLD = 25_000_000

        if is_self_occupied and properties_count == 1:
            return {
                'tax_7e_pkr': 0,
                'exempt': True,
                'exemption_reason': 'Single self-occupied residential house is fully exempt from Section 7E.',
                'withholding_tax_on_sale_pkr': int(fmv_pkr * (0.03 if filer_status == 'filer' else 0.06)),
                'advice': 'Your property is exempt from 7E. If you sell, withholding tax applies.',
                'notes': ['Self-occupied exemption applies to ONE house only.'],
            }

        notes = []

        if fmv_pkr <= THRESHOLD:
            tax_7e = 0
            notes.append(f'FMV PKR {fmv_pkr:,} is below PKR 25M threshold — no 7E tax liability.')
        else:
            rate = 0.01 if filer_status == 'filer' else 0.02
            tax_7e = int(fmv_pkr * rate)
            rate_label = '1%' if filer_status == 'filer' else '2% (non-filer surcharge)'
            notes.append(f'Rate: {rate_label} applied on PKR {fmv_pkr:,}')

        # Withholding tax if selling
        wht_rate = (0.02 if fmv_pkr <= 5_000_000 else 0.03) if filer_status == 'filer' else (0.04 if fmv_pkr <= 5_000_000 else 0.06)
        wht = int(fmv_pkr * wht_rate)

        # Stamp duty estimate (Punjab rates)
        stamp_duty = int(fmv_pkr * (0.03 if filer_status == 'filer' else 0.05))

        advice = (
            'Becoming an active FBR filer can halve your tax rates. Register at FBR IRIS portal (iris.fbr.gov.pk).'
            if filer_status == 'non_filer'
            else f'File annual income tax return by Sept 30 and declare this property at FMV PKR {fmv_pkr:,}.'
        )

        return {
            'fmv_pkr': fmv_pkr,
            'filer_status': filer_status,
            'tax_7e_annual_pkr': tax_7e,
            'exempt': tax_7e == 0,
            'exemption_reason': 'Below PKR 25M threshold' if fmv_pkr <= THRESHOLD else '',
            'withholding_tax_on_sale_pkr': wht,
            'stamp_duty_estimate_pkr': stamp_duty,
            'total_cost_if_selling_pkr': wht + stamp_duty,
            'advice': advice,
            'notes': notes,
        }
    except Exception as exc:
        logger.error(f"calculate_7e_tax tool failed: {exc}")
        return {'error': str(exc)}


# ─── Loan Eligibility ─────────────────────────────────────────────────────────

def check_loan_eligibility(
    monthly_income_pkr: int,
    loan_amount_pkr: int,
    tenure_years: int = 20,
    existing_emi_pkr: int = 0,
    scheme: str = 'conventional',
) -> dict:
    """
    Check home loan eligibility under Pakistan bank rules or Apna Ghar subsidized scheme.
    Call this when user asks about loan, mortgage, home financing, Apna Ghar scheme, or EMI.

    Args:
        monthly_income_pkr: Net monthly take-home income in PKR
        loan_amount_pkr: Required loan amount in PKR
        tenure_years: Repayment period in years (5 to 25)
        existing_emi_pkr: Existing monthly EMI/loan obligations in PKR (default 0)
        scheme: 'apna_ghar' for subsidized government scheme, 'conventional' for market rate banks
    """
    try:
        tenure_years = max(5, min(25, tenure_years))

        RATE = 0.07 if scheme == 'apna_ghar' else 0.22
        monthly_rate = RATE / 12
        n = tenure_years * 12

        if monthly_rate > 0:
            emi = int(loan_amount_pkr * (monthly_rate * (1 + monthly_rate)**n) / ((1 + monthly_rate)**n - 1))
        else:
            emi = loan_amount_pkr // n

        max_emi_allowed = int(monthly_income_pkr * 0.5) - existing_emi_pkr
        max_emi_allowed = max(0, max_emi_allowed)

        if monthly_rate > 0 and max_emi_allowed > 0:
            max_loan = int(max_emi_allowed * ((1 + monthly_rate)**n - 1) / (monthly_rate * (1 + monthly_rate)**n))
        else:
            max_loan = max_emi_allowed * n

        eligible = emi <= max_emi_allowed and monthly_income_pkr >= 25_000

        apna_ghar_eligible = (
            scheme == 'apna_ghar'
            and 25_000 <= monthly_income_pkr <= 200_000
            and loan_amount_pkr <= 10_000_000
        )

        if not eligible:
            if monthly_income_pkr < 25_000:
                reason = 'Monthly income below minimum requirement of PKR 25,000 for any home loan.'
            else:
                reason = f'EMI of PKR {emi:,}/month exceeds 50% income limit (PKR {max_emi_allowed:,}/month). Max loan: PKR {max_loan:,}.'
        else:
            reason = f'You qualify! Monthly EMI would be PKR {emi:,} on a {tenure_years}-year tenure.'

        next_steps = []
        if eligible:
            if scheme == 'apna_ghar' and apna_ghar_eligible:
                next_steps = [
                    'Apply at: HBL, UBL, Meezan Bank, Bank Alfalah, NBP, MCB, Bank of Punjab',
                    'Documents: CNIC, 6 months bank statements, salary slip or business proof',
                    'Property docs: Registry/Fard, NOC from society if applicable',
                    'Bring 2 guarantors with their CNICs and income proof',
                    'Also apply on NAPHDA portal at pmrc.com.pk',
                ]
            else:
                next_steps = [
                    'Compare rates from HBL, UBL, MCB, Bank Alfalah (shop for best rate)',
                    'Prepare: 6 months bank statements, CNIC, salary slip/business NTN',
                    'Property must be valued by bank-approved valuator',
                    'Minimum 30% down payment required',
                ]

        return {
            'eligible': eligible,
            'scheme': scheme,
            'apna_ghar_eligible': apna_ghar_eligible if scheme == 'apna_ghar' else None,
            'monthly_income_pkr': monthly_income_pkr,
            'loan_requested_pkr': loan_amount_pkr,
            'estimated_monthly_emi_pkr': emi,
            'max_affordable_loan_pkr': max_loan,
            'annual_interest_rate_percent': int(RATE * 100),
            'tenure_years': tenure_years,
            'reason': reason,
            'next_steps': next_steps,
        }
    except Exception as exc:
        logger.error(f"check_loan_eligibility tool failed: {exc}")
        return {'error': str(exc)}


# ─── Fraud Check ──────────────────────────────────────────────────────────────

def run_fraud_check(description: str) -> dict:
    """
    Analyze a property deal, agent, or transaction for fraud and scam indicators
    based on known Pakistani real estate scam patterns.
    Call this when user says 'check fraud', 'verify this agent', 'is this legit', 'scam check',
    or describes a suspicious deal or property situation.

    Args:
        description: Full description of the property deal, agent details, or suspicious situation
    """
    try:
        desc = description.lower()
        flags = []
        score = 0

        patterns = [
            ('advance payment',     'Advance payment demanded before documents shown — HIGH RISK',      45),
            ('token first',         'Token money demanded before any documents — Major red flag',        35),
            ('overseas',            'Overseas seller — NEVER send money without verified local presence', 25),
            ('urgent sale',         'Urgency pressure tactic — common manipulation technique',           15),
            ('kachhi file',         'Kachhi (unallocated) file — verify allocation with authority',       55),
            ('kachi file',          'Kachhi (unallocated) file — verify allocation with authority',       55),
            ('file not allotted',   'File not yet allotted — speculative, high risk',                    50),
            ('power of attorney',   'PoA involved — verify it is valid, registered, and not expired',    25),
            ('court case',          'Court litigation mentioned — DO NOT buy until resolved',             65),
            ('no fard',             'Seller unable to provide Fard — serious red flag',                   55),
            ('no documents',        'Seller has no documents — extremely high risk',                      70),
            ('below market',        'Price significantly below market — possible fraud or legal issue',   25),
            ('double sale',         'Possible double sale scenario',                                      60),
            ('no noc',              'No NOC from authority — registration cannot complete',               40),
            ('society not approved','Non-approved society — no LDA/CDA NOC',                             50),
        ]

        for keyword, flag_msg, risk_pts in patterns:
            if keyword in desc:
                if flag_msg not in flags:
                    flags.append(flag_msg)
                    score += risk_pts

        score = min(score, 100)
        risk = 'high' if score >= 50 else 'medium' if score >= 25 else 'low'

        verify_steps = [
            'Get Fard (ownership record) directly from PLRA, CDA, or local land records office',
            "Verify seller's CNIC matches all property documents",
            'Check for court orders at local civil courts (free record check)',
            'Physically visit the property and confirm boundaries with a witness',
            'For DHA/Bahria: Verify file/plot at the official authority office in person',
            'For any PoA: Verify at Sub-Registrar office that it is valid and not cancelled',
        ]

        recommendation = {
            'high': 'HIGH RISK — Strong fraud indicators detected. Consult a property lawyer BEFORE any payment. Do NOT transfer any money.',
            'medium': 'MEDIUM RISK — Proceed with caution. Verify all documents thoroughly before any payment.',
            'low': 'LOW RISK — Standard verification recommended. Always complete due diligence before finalizing.',
        }[risk]

        return {
            'risk': risk,
            'risk_score': score,
            'flags': flags,
            'recommendation': recommendation,
            'verify_steps': verify_steps[:4],
        }
    except Exception as exc:
        logger.error(f"run_fraud_check tool failed: {exc}")
        return {'risk': 'unknown', 'flags': [], 'error': str(exc)}


# ─── List Property ────────────────────────────────────────────────────────────

def list_property(
    city: str,
    location: str,
    area_marla: float,
    price_pkr: int,
    property_type: str,
    furnished: str = '',
    construction_status: str = '',
    description: str = '',
) -> dict:
    """
    Publish a new property listing on PakProp AI for buyers to discover.
    ONLY call this tool when you have collected ALL required information:
    city, location, area size (in marla), asking price (in PKR), and property type.
    If any required field is missing, ask the user for it first before calling this tool.

    Args:
        city: City where property is located (e.g., 'Lahore', 'Karachi')
        location: Specific area/society (e.g., 'DHA Phase 6', 'Gulberg 3', 'Bahria Town Block D')
        area_marla: Size in Marla. Convert Kanal to Marla (1 Kanal = 20 Marla).
        price_pkr: Asking price in Pakistani Rupees (e.g., 15000000 for PKR 1.5 crore)
        property_type: One of 'plot', 'residential', 'commercial'
        furnished: One of 'furnished', 'semi_furnished', 'unfurnished', or empty string
        construction_status: One of 'builder', 'ready', 'under_construction', or empty string
        description: Optional additional details or features
    """
    try:
        user = _ctx_user.get()

        from apps.properties.models import Property

        area_str = f"{area_marla}M " if area_marla else ""
        title = f"{area_str}{property_type.title()} — {location}, {city}"

        prop = Property.objects.create(
            owner=user,
            title=title,
            city=city,
            location=location,
            area_marla=area_marla if area_marla else None,
            price_pkr=price_pkr,
            property_type=property_type,
            furnished_status=furnished or None,
            construction_status=construction_status or None,
            description=description,
            legal_status=Property.LegalStatus.UNVERIFIED,
        )

        # Queue async AI scoring
        try:
            from apps.properties.tasks import score_property_task
            score_property_task.delay(str(prop.id))
        except Exception:
            pass

        # Capture lead
        try:
            from apps.whatsapp.handlers import _upsert_lead
            if user:
                _upsert_lead(user, 'sell', city_interest=city)
        except Exception:
            pass

        return {
            'success': True,
            'listing_id': str(prop.id)[:8].upper(),
            'title': title,
            'price_formatted': f"PKR {price_pkr:,}",
            'message': 'Property listed successfully. AI scoring is running in the background. Your listing is now visible to buyers.',
        }
    except Exception as exc:
        logger.error(f"list_property tool failed: {exc}")
        return {'success': False, 'error': 'Failed to create listing. Please try again.'}
