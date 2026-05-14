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
        phone = _ctx_phone.get() or ''
        results = PropertySearchService.search(
            city=city,
            location=location,
            area_marla=area_marla if area_marla > 0 else None,
            max_price=max_price_pkr if max_price_pkr > 0 else None,
            property_type=property_type,
            furnished_status=furnished,
            construction_status=construction_status,
            phone=phone,
        )
        if not results:
            return {
                'count': 0,
                'message': (
                    'No properties in our database match your criteria right now. '
                    'Live listings from Zameen and Graana are being fetched — '
                    'you will receive them in a follow-up message shortly.'
                ),
                'properties': [],
                'live_search_pending': True,
            }
        db_only = all(r.source == 'pakprop' for r in results)
        return {
            'count': len(results),
            'live_search_pending': db_only,  # signal AI to mention follow-up
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
            # English patterns
            ('advance payment',     'Advance payment demanded before documents shown — HIGH RISK',       45),
            ('token first',         'Token money demanded before any documents — Major red flag',         35),
            ('overseas',            'Overseas seller — NEVER send money without verified local presence',  25),
            ('urgent sale',         'Urgency pressure tactic — common manipulation technique',            15),
            ('kachhi file',         'Kachhi (unallocated) file — verify allocation with authority',        55),
            ('kachi file',          'Kachhi (unallocated) file — verify allocation with authority',        55),
            ('file not allotted',   'File not yet allotted — speculative, high risk',                     50),
            ('power of attorney',   'PoA involved — verify it is valid, registered, and not expired',     25),
            ('court case',          'Court litigation mentioned — DO NOT buy until resolved',              65),
            ('no fard',             'Seller unable to provide Fard — serious red flag',                    55),
            ('no documents',        'Seller has no documents — extremely high risk',                       70),
            ('below market',        'Price significantly below market — possible fraud or legal issue',    25),
            ('double sale',         'Possible double sale scenario',                                       60),
            ('no noc',              'No NOC from authority — registration cannot complete',                40),
            ('society not approved','Non-approved society — no LDA/CDA NOC',                              50),
            ('fake registry',       'Fake or forged registry document — verify at sub-registrar',         70),
            ('transfer fee waived', 'Transfer fee waiver claim — verify with authority directly',         30),
            ('deal expire',         'Artificial deadline — pressure tactic to rush payment',              20),
            ('limited time',        'Artificial deadline — pressure tactic to rush payment',              20),
            ('guaranteed return',   'Guaranteed return promise — no property investment is guaranteed',   35),
            # Romanized Urdu patterns
            ('pehle paise',         'Advance payment demanded before documents — HIGH RISK',              45),
            ('agay payment',        'Advance payment demanded before documents — HIGH RISK',              45),
            ('pehle token',         'Token money demanded before documents — Major red flag',             35),
            ('baher se',            'Overseas seller — NEVER send money without in-person verification',  25),
            ('bahir se',            'Overseas seller — NEVER send money without in-person verification',  25),
            ('jaldi karo',          'Urgency pressure — do not rush any property decision',               20),
            ('jaldi sale',          'Urgency pressure — do not rush any property decision',               20),
            ('kachha file',         'Kachhi (unallocated) file — verify allocation at authority office',  55),
            ('poa hai',             'PoA involved — verify it is valid, registered, and not expired',     25),
            ('fard nahi',           'Seller cannot provide Fard — serious red flag',                      55),
            ('documents nahi',      'No documents available — extremely high risk',                       70),
            ('sasta hai',           'Price below market — verify reason before any payment',              20),
            ('court mein hai',      'Property in court litigation — DO NOT proceed',                      65),
            ('noc nahi',            'No NOC from authority — transfer cannot complete',                   40),
            ('double bech',         'Possible double sale — get fresh Fard before any payment',           60),
            ('already sold',        'Possible double sale — get fresh Fard before any payment',           60),
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


# ─── Property Audit ──────────────────────────────────────────────────────────

def generate_property_audit(
    city: str,
    location: str,
    estimated_value_pkr: int,
    property_type: str = 'residential',
    area_marla: float = 0.0,
    owner_name: str = '',
    description: str = '',
) -> dict:
    """
    Generate a comprehensive property audit report with risk score, investment grade,
    market comparison, tax analysis, ROI projections, and role-specific insights.
    Call this when the user asks for 'property audit', 'audit report', 'property check',
    or wants a detailed analysis of a specific property.

    Args:
        city: City where the property is located e.g. 'Lahore', 'Karachi', 'Islamabad'
        location: Specific area e.g. 'DHA Phase 5', 'Bahria Town', 'Gulberg 3'
        estimated_value_pkr: Estimated property value in PKR (e.g. 15000000 for 1.5 crore)
        property_type: One of 'plot', 'house', 'apartment', 'commercial' (default: residential)
        area_marla: Size in Marla (0 if not known)
        owner_name: Owner name if known (optional)
        description: Any additional details, concerns, or context about the property
    """
    try:
        import os
        from django.conf import settings as django_settings
        from apps.audit.services import AuditEngine
        from apps.audit.pdf import generate_audit_pdf
        from apps.audit.models import PropertyAudit

        phone = _ctx_phone.get() or ''
        user  = _ctx_user.get()

        audit_data = AuditEngine.run(
            city=city,
            location=location,
            property_type=property_type,
            estimated_value_pkr=estimated_value_pkr,
            area_marla=area_marla if area_marla > 0 else None,
            owner_name=owner_name,
            description=description,
            phone=phone,
        )

        # Save to DB
        scores = audit_data['scores']
        audit  = PropertyAudit.objects.create(
            user=user,
            phone=phone,
            city=city,
            location=location,
            property_type=property_type,
            area_marla=area_marla if area_marla > 0 else None,
            estimated_value_pkr=estimated_value_pkr,
            owner_name=owner_name,
            description=description,
            risk_score=scores['risk_score'],
            investment_grade=scores['investment_grade'],
            liquidity_score=scores['liquidity_score'],
            audit_data=audit_data,
        )

        # Generate PDF
        media_root = django_settings.MEDIA_ROOT
        audits_dir = os.path.join(str(media_root), 'audits')
        os.makedirs(audits_dir, exist_ok=True)
        pdf_filename = f"audit_{audit.id}.pdf"
        pdf_path     = os.path.join(audits_dir, pdf_filename)

        generate_audit_pdf(audit_data, pdf_path)

        audit.pdf_file = f"audits/{pdf_filename}"
        audit.save(update_fields=['pdf_file'])

        base_url  = getattr(django_settings, 'BASE_URL', 'http://127.0.0.1:8000')
        pdf_url   = f"{base_url}/api/v1/audit/download/{audit.id}/"

        # Build WhatsApp summary
        ov  = audit_data['overview']
        fin = audit_data['financial_analysis']
        mkt = audit_data['market_analysis']
        rec = audit_data['recommendations']

        area_str  = f"{ov['area_marla']}M " if ov.get('area_marla') else ''
        value_str = f"PKR {estimated_value_pkr:,}"

        summary_lines = [
            f"🏠 *PROPERTY AUDIT REPORT*",
            f"📍 {area_str}{property_type.title()} — {location}, {city}",
            f"💰 Value: {value_str}",
            "",
            f"*Risk Score: {scores['risk_score']}/10 — {scores['risk_label']}*",
            f"*Investment Grade: {scores['investment_grade']} ({scores['investment_grade_label']})*",
            f"*Verdict: {scores['verdict']}* — _{scores['verdict_reason']}_",
            "",
            "📊 *MARKET ANALYSIS*",
            f"Price vs Market: {mkt['price_vs_market']}",
        ]

        if mkt.get('price_vs_market_pct') is not None:
            pct = mkt['price_vs_market_pct']
            summary_lines.append(f"Market Difference: {'+' if pct > 0 else ''}{pct:.1f}%")

        summary_lines += [
            f"Fair Value Range: PKR {mkt['estimated_fair_value_min']:,} – PKR {mkt['estimated_fair_value_max']:,}",
            "",
            "💰 *TAX OBLIGATIONS*",
        ]

        tax = fin['tax_table']
        if tax['7e_annual_filer'] > 0:
            summary_lines.append(f"7E Tax (filer): PKR {tax['7e_annual_filer']:,}/year")
        else:
            summary_lines.append("7E Tax: Exempt (below PKR 25M threshold)")
        summary_lines.append(f"WHT on sale (filer): PKR {tax['wht_filer']:,}")
        summary_lines.append(f"Stamp Duty: PKR {tax['stamp_duty_estimate']:,}")

        true_cost = fin['true_cost_buyer']['total']
        net_seller = fin['net_in_hand_seller']['net']
        summary_lines += [
            "",
            f"🏦 *FOR BUYER* — Total cost incl. fees: *PKR {true_cost:,}*",
            f"💼 *FOR SELLER* — Net in hand: *PKR {net_seller:,}*",
            f"📈 *5-YR PROJECTION* — PKR {fin['roi_projections']['5_year']['value']:,}",
            "",
            "⚠️ *TOP ACTIONS*",
        ]
        for i, action in enumerate(rec['top_3_actions'], 1):
            summary_lines.append(f"{i}. {action}")

        summary_lines += [
            "",
            f"📄 *Full PDF Report:* {pdf_url}",
            "",
            "_Consult a registered property lawyer and CA for final decisions._",
        ]

        return {
            'success': True,
            'audit_id': audit.id,
            'whatsapp_summary': '\n'.join(summary_lines),
            'pdf_url': pdf_url,
            'risk_score': scores['risk_score'],
            'investment_grade': scores['investment_grade'],
            'verdict': scores['verdict'],
        }

    except Exception as exc:
        logger.error(f"generate_property_audit failed: {exc}", exc_info=True)
        return {
            'success': False,
            'error': 'Audit generation failed. Please try again.',
            'whatsapp_summary': 'Sorry, I could not generate the audit report right now. Please try again.',
        }


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


# ─── Connect to Agent ─────────────────────────────────────────────────────────

def connect_to_agent(
    city: str = '',
    intent: str = 'buy',
    budget_pkr: int = 0,
    property_type: str = '',
    specific_area: str = '',
) -> dict:
    """
    Match the user with a verified real estate agent and return the agent's contact details.
    Call this when the user says 'talk to agent', 'connect me with an agent',
    'I want to speak to someone', 'refer me to an agent', 'I need an agent',
    or when they are clearly ready to proceed with buying, selling, or renting.

    Args:
        city: City user is interested in e.g. 'Lahore', 'Karachi', 'Islamabad', 'Rawalpindi'
        intent: User's intent — 'buy', 'sell', 'rent', or 'invest'
        budget_pkr: User's budget in PKR (0 if not mentioned)
        property_type: 'plot', 'house', 'apartment', 'commercial' (optional)
        specific_area: Specific area/society they mentioned e.g. 'DHA Phase 5' (optional)
    """
    try:
        from apps.agents.models import Agent
        from django.utils import timezone
        from django.db.models import Q

        phone = _ctx_phone.get() or ''
        user  = _ctx_user.get()

        # Build queryset — verified + active + available agents only
        qs = Agent.objects.filter(
            is_active=True,
            is_verified=True,
            availability_status=Agent.AvailabilityStatus.AVAILABLE,
        )

        # City match — STRICT: if city was specified, only return agents for that city.
        # Never return an agent from a different city just because no local one exists.
        if city:
            city_qs = qs.filter(cities__icontains=city)
            if not city_qs.exists():
                city_qs = qs.filter(primary_city__icontains=city)
            agents = city_qs  # may be empty — handled below as "no agent" case
        else:
            agents = qs       # no city specified → any verified agent

        # Specialization preference (soft match — prefer but don't filter out)
        spec_map = {
            'buy':        ['residential_buy', 'plots', 'luxury', 'new_projects'],
            'sell':       ['residential_buy', 'plots', 'commercial', 'luxury'],
            'rent':       ['residential_rent', 'commercial'],
            'invest':     ['plots', 'new_projects', 'residential_buy', 'commercial'],
            'commercial': ['commercial', 'industrial'],
        }
        preferred_specs = spec_map.get(intent.lower(), [])

        # Load-balanced selection: featured first, then fewest recent leads (last 7 days),
        # then highest rating. This distributes leads evenly within the same tier.
        def _pick_agent(qs):
            from django.utils import timezone as _tz
            from datetime import timedelta
            from django.db.models import Count, Q as _Q
            week_ago = _tz.now() - timedelta(days=7)
            return (
                qs.annotate(
                    recent_leads=Count(
                        'assigned_leads',
                        filter=_Q(assigned_leads__created_at__gte=week_ago),
                    )
                )
                .order_by('-is_featured', 'recent_leads', '-rating')
                .first()
            )

        spec_agent = None
        for spec in preferred_specs:
            spec_qs = agents.filter(specializations__icontains=spec)
            if spec_qs.exists():
                spec_agent = _pick_agent(spec_qs)
                break

        agent = spec_agent or _pick_agent(agents)

        # No agents available for the requested city (or at all)
        if not agent:
            _capture_agent_request_lead(user, phone, city, intent, budget_pkr, specific_area)
            city_str = f"*{city}*" if city else "your area"
            no_agent_msg = (
                f"We don't have a verified agent registered for {city_str} yet.\n\n"
                "Your request has been noted. Our team will connect you with an "
                "authorized PakProp AI agent for your area shortly — we'll reach out "
                "to you on this WhatsApp number."
            )
            # INSTRUCTION FOR MODEL: return this message verbatim — do not add any agent details
            return {
                'found': False,
                'message': no_agent_msg,
                'whatsapp_summary': no_agent_msg,
                '_instruction': 'Return the whatsapp_summary above VERBATIM. Do NOT add any agent names, phone numbers, or contact details.',
            }

        # Update agent metrics
        Agent.objects.filter(pk=agent.pk).update(
            total_leads=agent.total_leads + 1,
            last_active_at=timezone.now(),
        )

        # Capture lead
        _capture_agent_request_lead(user, phone, city, intent, budget_pkr, specific_area,
                                    agent=agent)

        # Build WhatsApp agent card
        lines = [
            f"✅ *Agent Found!*",
            "",
        ]

        if agent.agent_type == Agent.AgentType.INDIVIDUAL:
            lines.append(f"👤 *{agent.name}*")
        else:
            lines.append(f"🏢 *{agent.name}*")

        if agent.company_name:
            lines.append(f"   {agent.company_name} ({agent.get_agent_type_display()})")

        if agent.designation:
            lines.append(f"   {agent.designation}")

        lines.append("")

        # Coverage
        cities_display = ', '.join(agent.cities[:3]) if agent.cities else agent.primary_city
        areas_display  = ', '.join(agent.areas[:4]) if agent.areas else ''
        if cities_display:
            coverage = f"📍 *Cities:* {cities_display}"
            if areas_display:
                coverage += f"\n   *Areas:* {areas_display}"
            lines.append(coverage)

        # Specializations
        if agent.specializations_str and agent.specializations_str != '—':
            lines.append(f"💼 *Specializes in:* {agent.specializations_str}")

        # Experience + rating
        exp_parts = []
        if agent.years_experience:
            exp_parts.append(f"{agent.years_experience} years experience")
        if float(agent.rating) > 0:
            exp_parts.append(f"⭐ {agent.rating}/5 rating")
        if agent.closed_deals:
            exp_parts.append(f"{agent.closed_deals} deals closed")
        if exp_parts:
            lines.append(f"📊 {' · '.join(exp_parts)}")

        if agent.license_number:
            lines.append(f"🪪 License: {agent.license_number}")

        lines.append("")
        lines.append(f"📞 *WhatsApp: {agent.contact_whatsapp}*")

        if agent.email:
            lines.append(f"📧 {agent.email}")
        if agent.website:
            lines.append(f"🌐 {agent.website}")
        if agent.office_address:
            lines.append(f"🏢 {agent.office_address}")

        if agent.instagram_handle:
            lines.append(f"📸 @{agent.instagram_handle}")

        lines += [
            "",
            "✅ *Verified by PakProp AI*",
            "",
            "_Feel free to contact them directly on WhatsApp. "
            "Mention PakProp AI when you reach out._",
        ]

        if agent.bio:
            lines += ["", f"_{agent.bio}_"]

        summary = '\n'.join(lines)

        # INSTRUCTION FOR MODEL: return whatsapp_summary VERBATIM — do not modify any details
        return {
            'found': True,
            'agent_id': agent.id,
            'agent_name': agent.name,
            'agent_whatsapp': agent.contact_whatsapp,
            'whatsapp_summary': summary,
            'message': summary,
            '_instruction': 'Return the whatsapp_summary field EXACTLY as shown. Do NOT change any names, numbers, or details.',
        }

    except Exception as exc:
        logger.error(f"connect_to_agent tool failed: {exc}", exc_info=True)
        return {
            'found': False,
            'error': str(exc),
            'whatsapp_summary': 'Sorry, I could not find an agent right now. Please try again.',
        }


def _capture_agent_request_lead(user, phone: str, city: str, intent: str,
                                 budget_pkr: int, specific_area: str,
                                 agent=None):
    try:
        from apps.leads.models import Lead
        intent_map = {
            'buy': Lead.Intent.BUY, 'sell': Lead.Intent.SELL,
            'rent': Lead.Intent.RENT, 'invest': Lead.Intent.INVEST,
        }
        lead_intent = intent_map.get(intent.lower(), Lead.Intent.BUY)
        signals = {'source': 'talk_to_agent', 'specific_area': specific_area}
        if agent:
            signals['matched_agent_id'] = agent.id
        if user:
            lead, _ = Lead.objects.update_or_create(
                user=user, intent=lead_intent,
                defaults={
                    'city_interest':  city,
                    'budget_max':     budget_pkr if budget_pkr > 0 else None,
                    'intent_signals': signals,
                    'score':          80,
                    'status':         Lead.Status.QUALIFIED,
                },
            )
            if agent and not lead.assigned_agent_id:
                lead.assigned_agent = agent
                lead.save(update_fields=['assigned_agent'])
    except Exception as exc:
        logger.error(f"_capture_agent_request_lead failed: {exc}")


# ─── Deal Lock ────────────────────────────────────────────────────────────────

def initiate_deal_lock(
    property_id: str,
    token_amount_pkr: int,
    payment_method: str = 'jazzcash',
) -> dict:
    """
    Lock a property exclusively for the buyer for 48 hours by paying a token amount.
    Use this when a user says they want to 'lock', 'reserve', 'book token', or 'secure' a property.

    Args:
        property_id: The UUID of the property to lock (from search results).
        token_amount_pkr: Token amount in PKR. Must be between 25,000 and 100,000.
        payment_method: Payment method — one of 'jazzcash', 'easypaisa', 'bank', 'manual'.
    """
    user  = _ctx_user.get()
    phone = _ctx_phone.get()

    if not user or not phone:
        return {'success': False, 'message': 'Could not identify your account. Please try again.'}

    if token_amount_pkr < 25_000 or token_amount_pkr > 100_000:
        return {
            'success': False,
            'message': (
                "Token amount must be between *PKR 25,000* and *PKR 100,000*.\n"
                "Please specify an amount in this range."
            ),
        }

    valid_methods = {'jazzcash', 'easypaisa', 'bank', 'manual'}
    if payment_method not in valid_methods:
        payment_method = 'jazzcash'

    try:
        from apps.properties.models import Property
        from apps.escrow.models import EscrowDeal

        try:
            prop = Property.objects.get(id=property_id, is_active=True)
        except Property.DoesNotExist:
            return {'success': False, 'message': 'Property not found. Please search again and use the exact property ID.'}

        # Check for existing active lock
        existing = EscrowDeal.objects.filter(
            property=prop,
            status__in=[EscrowDeal.Status.INITIATED, EscrowDeal.Status.LOCKED]
        ).first()
        if existing:
            if existing.buyer == user:
                return {
                    'success': False,
                    'message': (
                        f"You already have an active deal lock on *{prop.title}*.\n"
                        f"Status: *{existing.get_status_display()}*"
                    ),
                }
            return {
                'success': False,
                'message': (
                    f"⚠️ *{prop.title}* is currently locked by another buyer.\n"
                    "Please check back after the lock expires (within 48 hours)."
                ),
            }

        deal = EscrowDeal.objects.create(
            property        = prop,
            buyer           = user,
            token_amount    = token_amount_pkr,
            payment_gateway = payment_method,
            initiated_via   = EscrowDeal.Channel.WHATSAPP,
            status          = EscrowDeal.Status.INITIATED,
        )

        # Try to generate an online payment link if an online gateway is configured
        online_link = ''
        from apps.config.services import SystemConfigService
        active_gw = SystemConfigService.get_active_gateway()
        if active_gw in ('safepay', 'bsecure'):
            try:
                from apps.payments.services import PaymentService
                base_url = SystemConfigService.get('base_url')
                result = PaymentService.create_checkout(
                    deal=deal,
                    gateway=active_gw,
                    redirect_url=f"{base_url}/payments/return/?status=success&deal_id={deal.id}",
                    cancel_url=f"{base_url}/payments/return/?status=cancelled&deal_id={deal.id}",
                )
                online_link = result.get('checkout_url', '')
            except Exception as exc:
                logger.warning(f"Could not create {active_gw} checkout for deal {deal.id}: {exc}")

        _PAYMENT_INSTRUCTIONS = {
            'jazzcash':  f"Send *PKR {token_amount_pkr:,}* to JazzCash *03001234567*. Use your WhatsApp number as reference.",
            'easypaisa': f"Send *PKR {token_amount_pkr:,}* to EasyPaisa *03001234567*. Use your WhatsApp number as reference.",
            'bank':      f"Transfer *PKR {token_amount_pkr:,}* to Account *1234567890* (HBL — PakProp AI). Reference: your WhatsApp number.",
            'manual':    "Our team will contact you with payment details within 1 hour.",
        }
        payment_msg = _PAYMENT_INSTRUCTIONS.get(payment_method, _PAYMENT_INSTRUCTIONS['manual'])

        online_section = (
            f"\n💳 *Pay Online (instant):*\n{online_link}\n"
        ) if online_link else ''

        summary = (
            f"🔒 *Deal Lock Requested!*\n\n"
            f"🏠 *Property:* {prop.title}\n"
            f"📍 *Location:* {prop.city} — {prop.location}\n"
            f"💰 *Token Amount:* PKR {token_amount_pkr:,}\n"
            f"🔑 *Lock ID:* `{str(deal.id)[:8].upper()}`\n\n"
            f"*Payment Instructions:*\n{payment_msg}"
            f"{online_section}\n\n"
            "✅ Once payment is confirmed, your *48-hour exclusivity* window begins automatically.\n"
            "You will receive a WhatsApp confirmation immediately."
        )

        return {
            'success':      True,
            'deal_id':      str(deal.id),
            'property':     prop.title,
            'token_amount': token_amount_pkr,
            'whatsapp_summary': summary,
            '_instruction': 'Return the whatsapp_summary VERBATIM.',
        }

    except Exception as exc:
        logger.error(f"initiate_deal_lock failed: {exc}", exc_info=True)
        return {
            'success': False,
            'message': 'Could not process your deal lock request. Please try again.',
        }
