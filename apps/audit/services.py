"""
Audit Engine — generates a full structured property audit report.
Uses Pakistani real-estate benchmarks hardcoded at module level.
No AI calls — pure deterministic scoring; fast and free.
"""
import math
import logging

logger = logging.getLogger(__name__)

# ─── Benchmarks ───────────────────────────────────────────────────────────────
# Keys:  price_per_marla range (PKR min, max),
#        rental yield %,
#        annual appreciation %,
#        avg months to sell,
#        area_approved (True/False/None)

BENCHMARKS = {
    'lahore': {
        'dha':        {'ppm': (4_000_000, 12_000_000), 'yield_pct': 3.5, 'appr_pct': 12, 'liq_months': 2,  'approved': True},
        'bahria':     {'ppm': (2_500_000,  5_000_000), 'yield_pct': 4.5, 'appr_pct': 15, 'liq_months': 2,  'approved': True},
        'gulberg':    {'ppm': (5_000_000, 15_000_000), 'yield_pct': 3.0, 'appr_pct':  8, 'liq_months': 3,  'approved': True},
        'model town': {'ppm': (3_000_000,  8_000_000), 'yield_pct': 3.5, 'appr_pct':  8, 'liq_months': 4,  'approved': True},
        'johar town': {'ppm': (2_000_000,  4_000_000), 'yield_pct': 4.0, 'appr_pct': 10, 'liq_months': 3,  'approved': True},
        'wapda town': {'ppm': (1_800_000,  3_500_000), 'yield_pct': 4.5, 'appr_pct':  9, 'liq_months': 4,  'approved': True},
        'default':    {'ppm': (1_500_000,  3_000_000), 'yield_pct': 4.0, 'appr_pct':  8, 'liq_months': 5,  'approved': None},
    },
    'islamabad': {
        'dha':        {'ppm': (6_000_000, 20_000_000), 'yield_pct': 3.0, 'appr_pct': 10, 'liq_months': 3,  'approved': True},
        'bahria':     {'ppm': (3_000_000,  7_000_000), 'yield_pct': 4.0, 'appr_pct': 12, 'liq_months': 2,  'approved': True},
        'f-7':        {'ppm': (10_000_000, 30_000_000), 'yield_pct': 2.5, 'appr_pct':  6, 'liq_months': 6,  'approved': True},
        'e-7':        {'ppm': (8_000_000, 25_000_000), 'yield_pct': 2.5, 'appr_pct':  6, 'liq_months': 6,  'approved': True},
        'g-11':       {'ppm': (3_000_000,  6_000_000), 'yield_pct': 4.0, 'appr_pct':  8, 'liq_months': 4,  'approved': True},
        'default':    {'ppm': (2_000_000,  5_000_000), 'yield_pct': 4.0, 'appr_pct':  8, 'liq_months': 4,  'approved': None},
    },
    'karachi': {
        'dha':        {'ppm': (5_000_000, 15_000_000), 'yield_pct': 4.0, 'appr_pct':  8, 'liq_months': 3,  'approved': True},
        'bahria':     {'ppm': (2_000_000,  5_000_000), 'yield_pct': 5.0, 'appr_pct': 10, 'liq_months': 3,  'approved': True},
        'clifton':    {'ppm': (8_000_000, 25_000_000), 'yield_pct': 3.5, 'appr_pct':  7, 'liq_months': 5,  'approved': True},
        'gulshan':    {'ppm': (2_000_000,  5_000_000), 'yield_pct': 4.5, 'appr_pct':  7, 'liq_months': 4,  'approved': True},
        'defence':    {'ppm': (5_000_000, 15_000_000), 'yield_pct': 4.0, 'appr_pct':  8, 'liq_months': 3,  'approved': True},
        'default':    {'ppm': (1_500_000,  4_000_000), 'yield_pct': 4.5, 'appr_pct':  7, 'liq_months': 5,  'approved': None},
    },
    'rawalpindi': {
        'bahria':     {'ppm': (2_500_000,  6_000_000), 'yield_pct': 4.5, 'appr_pct': 12, 'liq_months': 3,  'approved': True},
        'dha':        {'ppm': (4_000_000, 10_000_000), 'yield_pct': 3.5, 'appr_pct': 10, 'liq_months': 3,  'approved': True},
        'satellite':  {'ppm': (1_500_000,  3_000_000), 'yield_pct': 4.5, 'appr_pct':  8, 'liq_months': 5,  'approved': True},
        'default':    {'ppm': (1_000_000,  2_500_000), 'yield_pct': 4.5, 'appr_pct':  8, 'liq_months': 6,  'approved': None},
    },
    'default': {
        'default':    {'ppm': (1_000_000,  3_000_000), 'yield_pct': 4.0, 'appr_pct':  8, 'liq_months': 6,  'approved': None},
    },
}


def _get_benchmark(city: str, location: str) -> dict:
    """
    Return the best-matching benchmark entry for (city, location).
    Queries the DB (admin-configurable) first; falls back to hardcoded defaults.
    DB lookup order: city+location match → city default → global default → hardcoded.
    """
    city_key = city.lower().strip()
    loc_key = location.lower().strip()

    try:
        from apps.audit.models import AuditBenchmark
        rows = list(
            AuditBenchmark.objects.filter(
                city__in=[city_key, 'default'],
                is_active=True,
            )
        )
        if rows:
            city_rows = [r for r in rows if r.city == city_key]
            # Prefer city-specific location match
            for r in city_rows:
                if r.location_key != 'default' and r.location_key in loc_key:
                    return r.to_dict()
            # City-level default
            for r in city_rows:
                if r.location_key == 'default':
                    return r.to_dict()
            # Global default from DB
            for r in rows:
                if r.city == 'default' and r.location_key == 'default':
                    return r.to_dict()
    except Exception:
        pass

    # ── Hardcoded fallback (used when DB is empty or unavailable) ──────────────
    city_data = BENCHMARKS.get(city_key, BENCHMARKS['default'])
    for bench_key, bench_val in city_data.items():
        if bench_key == 'default':
            continue
        if bench_key in loc_key:
            return bench_val
    if 'default' in city_data:
        return city_data['default']
    return BENCHMARKS['default']['default']


# ─── Fraud patterns (copied from apps/ai/tools.py to avoid circular imports) ──

_FRAUD_PATTERNS = [
    ('advance payment',      'Advance payment demanded before documents shown — HIGH RISK',       45),
    ('token first',          'Token money demanded before any documents — Major red flag',         35),
    ('overseas',             'Overseas seller — NEVER send money without verified local presence', 25),
    ('urgent sale',          'Urgency pressure tactic — common manipulation technique',            15),
    ('kachhi file',          'Kachhi (unallocated) file — verify allocation with authority',        55),
    ('kachi file',           'Kachhi (unallocated) file — verify allocation with authority',        55),
    ('file not allotted',    'File not yet allotted — speculative, high risk',                     50),
    ('power of attorney',    'PoA involved — verify it is valid, registered, and not expired',     25),
    ('court case',           'Court litigation mentioned — DO NOT buy until resolved',              65),
    ('no fard',              'Seller unable to provide Fard — serious red flag',                    55),
    ('no documents',         'Seller has no documents — extremely high risk',                       70),
    ('below market',         'Price significantly below market — possible fraud or legal issue',    25),
    ('double sale',          'Possible double sale scenario',                                       60),
    ('no noc',               'No NOC from authority — registration cannot complete',                40),
    ('society not approved', 'Non-approved society — no LDA/CDA NOC',                              50),
]


def _run_fraud_check(description: str) -> dict:
    desc = description.lower()
    flags = []
    score = 0
    for keyword, flag_msg, risk_pts in _FRAUD_PATTERNS:
        if keyword in desc:
            if flag_msg not in flags:
                flags.append(flag_msg)
                score += risk_pts
    score = min(score, 100)
    risk = 'high' if score >= 50 else 'medium' if score >= 25 else 'low'
    return {'risk': risk, 'score': score, 'flags': flags}


# ─── Audit Engine ─────────────────────────────────────────────────────────────

class AuditEngine:

    @classmethod
    def run(
        cls,
        city: str,
        location: str,
        property_type: str,
        estimated_value_pkr: int,
        area_marla: float = None,
        owner_name: str = '',
        description: str = '',
        phone: str = '',
    ) -> dict:
        """
        Generate a full structured property audit report.
        Returns a large dict with overview, scores, market_analysis,
        financial_analysis, legal_checklist, fraud_indicators,
        role_insights, and recommendations sections.
        """
        bench = _get_benchmark(city, location)
        loc_lower = location.lower()
        city_lower = city.lower()
        desc_lower = description.lower()
        ptype_lower = property_type.lower()

        # ── Basic derived values ──────────────────────────────────────────────
        price_per_marla = (
            int(estimated_value_pkr / float(area_marla)) if area_marla and float(area_marla) > 0 else None
        )
        ppm_min, ppm_max = bench['ppm']
        market_avg_ppm = int((ppm_min + ppm_max) / 2)
        appr_pct = bench['appr_pct']
        yield_pct = bench['yield_pct']
        liq_months = bench['liq_months']
        area_approved = bench['approved']

        # price vs market
        if price_per_marla and area_marla:
            pct_diff = ((price_per_marla - market_avg_ppm) / market_avg_ppm) * 100
        elif area_marla:
            pct_diff = 0.0
        else:
            pct_diff = 0.0

        if pct_diff > 15:
            price_vs_market = 'ABOVE MARKET'
        elif pct_diff < -15:
            price_vs_market = 'BELOW MARKET'
        else:
            price_vs_market = 'FAIR'

        # fair value range
        if area_marla and float(area_marla) > 0:
            fair_min = int(ppm_min * float(area_marla))
            fair_max = int(ppm_max * float(area_marla))
        else:
            fair_min = ppm_min
            fair_max = ppm_max

        # ── Risk Score ────────────────────────────────────────────────────────
        risk = 2

        if price_per_marla:
            over_pct = pct_diff
            if over_pct > 30:
                risk += 3
            elif over_pct > 15:
                risk += 2
        else:
            risk += 1  # unknown area / no marla given

        scam_keywords = ['advance', 'kachi file', 'kachhi file', 'court case', 'no documents', 'overseas']
        scam_hits = 0
        for kw in scam_keywords:
            if kw in desc_lower:
                scam_hits += 2
                if scam_hits >= 4:
                    break
        risk += min(scam_hits, 4)

        if 'under construction' in desc_lower or 'under_construction' in ptype_lower:
            risk += 1

        positive_keywords = ['verified', 'approved', 'dha', 'bahria']
        for kw in positive_keywords:
            if kw in desc_lower or kw in loc_lower:
                risk -= 1

        risk = max(1, min(10, risk))

        # ── Investment Grade ──────────────────────────────────────────────────
        if risk <= 3 and area_approved is True:
            grade = 'A'
            grade_label = 'Excellent — Prime investment, strong fundamentals'
        elif risk <= 5 or area_approved is True:
            grade = 'B'
            grade_label = 'Good — Solid investment with moderate risk'
        elif risk <= 7:
            grade = 'C'
            grade_label = 'Fair — Invest with caution, verify all documents'
        else:
            grade = 'D'
            grade_label = 'Poor — High risk, major red flags detected'

        # ── Liquidity Score ───────────────────────────────────────────────────
        if liq_months <= 2:
            liq = 9
        elif liq_months == 3:
            liq = 7
        elif liq_months == 4:
            liq = 6
        elif liq_months == 5:
            liq = 5
        else:
            liq = 4

        if 'commercial' in ptype_lower:
            liq = max(1, liq - 2)

        # ── Verdict ───────────────────────────────────────────────────────────
        if grade == 'A' and risk <= 3:
            verdict = 'BUY'
            verdict_reason = (
                f"Strong fundamentals in {location}, {city}. "
                f"Approved area, competitive price, high liquidity. Proceed after standard due diligence."
            )
        elif grade == 'B' and risk <= 5:
            verdict = 'CONSIDER'
            verdict_reason = (
                f"Decent opportunity in {location}, {city}. "
                f"Moderate risk profile. Verify documents thoroughly before commitment."
            )
        elif grade == 'C':
            verdict = 'CAUTION'
            verdict_reason = (
                f"Multiple risk factors present. {location}, {city} requires careful due diligence. "
                f"Consult a property lawyer before proceeding."
            )
        else:
            verdict = 'AVOID'
            verdict_reason = (
                f"High risk profile detected for this property in {location}, {city}. "
                f"Significant concerns need resolution before any payment."
            )

        risk_label = 'LOW' if risk <= 3 else 'MEDIUM' if risk <= 6 else 'HIGH'
        risk_color = 'GREEN' if risk <= 3 else 'YELLOW' if risk <= 6 else 'RED'
        liq_label = 'High' if liq >= 8 else 'Medium' if liq >= 5 else 'Low'

        # ── Financial Analysis ────────────────────────────────────────────────
        val = estimated_value_pkr

        stamp_duty = int(val * 0.03)
        reg_fee = int(val * 0.01)
        legal_fees_buyer = 50_000
        agent_comm = int(val * 0.01)
        true_cost_total = val + stamp_duty + reg_fee + legal_fees_buyer + agent_comm

        wht_seller = int(val * 0.03)
        legal_fees_seller = 30_000
        net_seller = val - wht_seller - agent_comm - legal_fees_seller

        # Tax table
        gain_assumed = int(val * 0.15)
        tax_7e_filer = int(val * 0.01) if val > 25_000_000 else 0
        tax_7e_nonfiler = int(val * 0.02) if val > 25_000_000 else 0
        cgt_1yr = int(gain_assumed * 0.15)
        cgt_2yr = int(gain_assumed * 0.125)
        cgt_3yr = int(gain_assumed * 0.10)
        cgt_4yr = 0
        wht_filer = int(val * 0.03) if val > 5_000_000 else 0
        wht_nonfiler = int(val * 0.06)

        # Rental
        monthly_rent = int(val * yield_pct / 100 / 12)
        annual_rental = monthly_rent * 12
        gross_yield = yield_pct
        net_yield = round(gross_yield * 0.85, 2)

        # Loan to buy
        rate = 0.22
        monthly_rate = rate / 12
        n = 20 * 12
        loan_amt = int(val * 0.70)
        down_pmt = int(val * 0.30)
        emi = int(loan_amt * (monthly_rate * (1 + monthly_rate) ** n) / ((1 + monthly_rate) ** n - 1))
        min_income = int(emi / 0.5)

        # ROI projections
        def _future_value(v, rate_pct, years):
            fv = int(v * (1 + rate_pct / 100) ** years)
            gain = fv - v
            gain_pct = round((gain / v) * 100, 1)
            return {'value': fv, 'gain': gain, 'gain_pct': gain_pct}

        roi_1yr = _future_value(val, appr_pct, 1)
        roi_3yr = _future_value(val, appr_pct, 3)
        roi_5yr = _future_value(val, appr_pct, 5)

        # ── Legal Checklist ───────────────────────────────────────────────────
        if 'dha' in loc_lower:
            reg_authority = 'DHA Offices'
        elif 'bahria' in loc_lower:
            reg_authority = 'Bahria Town Office'
        elif 'cda' in loc_lower or city_lower == 'islamabad':
            reg_authority = 'CDA (Capital Development Authority)'
        else:
            reg_authority = 'Sub-Registrar / PLRA'

        noc_note = (
            'NOC from DHA required before transfer.' if 'dha' in loc_lower
            else 'NOC from Bahria Town office required.' if 'bahria' in loc_lower
            else 'NOC from LDA/CDA/RDA required for approved societies.'
        )
        approval_note = (
            f"{location} is an approved housing scheme." if area_approved is True
            else 'Verify approval status with local development authority (LDA/CDA/RDA).'
        )

        legal_items = [
            {'label': 'Society/Authority Approval', 'status': 'VERIFY', 'note': approval_note},
            {'label': 'Fard / Ownership Record', 'status': 'REQUIRED', 'note': 'Get from PLRA/CDA/DDA'},
            {'label': 'CNIC Verification', 'status': 'REQUIRED', 'note': 'Seller CNIC must match all docs'},
            {'label': 'No Objection Certificate (NOC)', 'status': 'VERIFY', 'note': noc_note},
            {'label': 'Court / Litigation Check', 'status': 'REQUIRED', 'note': 'Check local civil courts'},
            {'label': 'Utility Dues Clearance', 'status': 'VERIFY', 'note': 'WAPDA, SNGPL, PTCL bills'},
            {'label': 'Capital Value Tax Clearance', 'status': 'REQUIRED', 'note': 'CVT receipt from FBR'},
            {'label': 'Transfer/Mutation (Intiqal)', 'status': 'REQUIRED', 'note': 'Must be done at Sub-Registrar'},
        ]

        docs_buyer = [
            'Original CNIC (buyer + 2 witnesses)',
            'Passport-size photographs (4)',
            'Bank statement (6 months)',
            'Payment proof / bank draft',
            'Sale Deed / Agreement to Sell (stamped)',
            'Fard (ownership record)',
            'NOC from housing authority (if applicable)',
            'Tax clearance certificate (FBR)',
        ]
        docs_seller = [
            'Original CNIC',
            'Original Fard / Title Deed',
            'Original allotment letter (if applicable)',
            'All previous sale deeds / chain of title',
            'NOC from housing authority',
            'Utility bill clearance certificates',
            'CVT (Capital Value Tax) payment receipt',
            'NTN (National Tax Number) certificate',
        ]

        # ── Fraud Indicators ──────────────────────────────────────────────────
        fraud = _run_fraud_check(description)

        # ── Role Insights ─────────────────────────────────────────────────────
        if pct_diff > 10:
            negotiation_tip = 'Property is above market — negotiate down 10-15%.'
        elif pct_diff < -10:
            negotiation_tip = 'Property is below market — act quickly.'
        else:
            negotiation_tip = 'Price is fair — minor negotiation of 3-5% is reasonable.'

        buyer_action_items = [
            f"Get Fard from {reg_authority} before paying any token",
            'Verify seller CNIC matches all property documents',
            f"True cost including all taxes and fees: PKR {true_cost_total:,}",
            f"Plan for 30% down payment: PKR {down_pmt:,}",
        ]

        if verdict == 'BUY':
            buyer_action_items.append('Strong buy signal — move quickly, this area has high demand')
        elif verdict == 'AVOID':
            buyer_action_items.append('Major red flags — consult a property lawyer before any payment')

        seller_action_items = [
            'Collect all original documents: Fard, allotment letter, chain of title',
            'Get utility clearance certificates before listing',
            'Register with FBR as filer to reduce withholding tax from 6% to 3%',
            f"List at PKR {fair_max:,} based on area benchmark",
        ]

        best_time_to_sell = (
            'After 4 years of ownership (CGT drops to 0% for filers)'
            if risk <= 5
            else 'Resolve legal concerns before listing'
        )

        dev_potential = (
            'High development potential — approved area with strong infrastructure'
            if area_approved is True
            else 'Moderate potential — verify approval status and utility availability'
        )

        if 'house' in ptype_lower or 'residential' in ptype_lower or 'plot' in ptype_lower:
            permissible_floors = 'Ground + 2 floors (residential zone)'
        elif 'commercial' in ptype_lower:
            permissible_floors = 'Ground + 4–6 floors (commercial zone)'
        else:
            permissible_floors = 'Ground + 2 floors (subject to local building bylaws)'

        # Context-specific third recommendation
        if fraud['risk'] == 'high':
            third_action = 'HIGH FRAUD RISK — Do not transfer any payment until all documents are independently verified'
        elif verdict == 'AVOID':
            third_action = 'Consult a registered property lawyer before proceeding with this transaction'
        elif price_vs_market == 'ABOVE MARKET':
            third_action = f"Negotiate price down — property is {abs(pct_diff):.1f}% above market rate"
        elif area_approved is None:
            third_action = 'Verify area approval status with local development authority (LDA/CDA/RDA)'
        else:
            third_action = f"Check ROI: expected {appr_pct}% annual appreciation — 5-year projection PKR {roi_5yr['value']:,}"

        green_flags = []
        red_flags = []

        if area_approved is True:
            green_flags.append(f'{location} is an LDA/CDA approved area')
        if liq <= 2:
            green_flags.append('High liquidity — properties in this area sell within 2 months')
        if appr_pct >= 12:
            green_flags.append(f'{appr_pct}% annual appreciation — strong capital growth market')
        if price_vs_market == 'BELOW MARKET':
            green_flags.append(f"Price is {abs(pct_diff):.1f}% below market — potential deal")
        if grade in ('A', 'B'):
            green_flags.append(f'Investment Grade {grade} — {grade_label}')

        if fraud['flags']:
            red_flags.extend(fraud['flags'][:3])
        if price_vs_market == 'ABOVE MARKET':
            red_flags.append(f"Price is {pct_diff:.1f}% above market rate — overpaying risk")
        if area_approved is None:
            red_flags.append('Area approval status unverified — check with local authority')
        if risk >= 7:
            red_flags.append(f'High risk score ({risk}/10) — proceed with extreme caution')

        # ── Final Assembly ────────────────────────────────────────────────────
        return {
            'overview': {
                'city': city,
                'location': location,
                'property_type': property_type,
                'area_marla': float(area_marla) if area_marla else None,
                'estimated_value_pkr': estimated_value_pkr,
                'owner_name': owner_name,
                'price_per_marla': price_per_marla,
            },
            'scores': {
                'risk_score': risk,
                'risk_label': risk_label,
                'risk_color': risk_color,
                'investment_grade': grade,
                'investment_grade_label': grade_label,
                'liquidity_score': liq,
                'liquidity_label': liq_label,
                'verdict': verdict,
                'verdict_reason': verdict_reason,
            },
            'market_analysis': {
                'benchmark_ppm_min': ppm_min,
                'benchmark_ppm_max': ppm_max,
                'your_ppm': price_per_marla,
                'market_avg_ppm': market_avg_ppm,
                'price_vs_market': price_vs_market,
                'price_vs_market_pct': round(pct_diff, 1),
                'area_approved': area_approved,
                'comparable_listings': [],
                'estimated_fair_value_min': fair_min,
                'estimated_fair_value_max': fair_max,
            },
            'financial_analysis': {
                'estimated_value_pkr': val,
                'true_cost_buyer': {
                    'asking_price': val,
                    'stamp_duty': stamp_duty,
                    'registration_fee': reg_fee,
                    'legal_fees': legal_fees_buyer,
                    'agent_commission': agent_comm,
                    'total': true_cost_total,
                },
                'net_in_hand_seller': {
                    'asking_price': val,
                    'withholding_tax': wht_seller,
                    'agent_commission': agent_comm,
                    'legal_fees': legal_fees_seller,
                    'net': net_seller,
                },
                'tax_table': {
                    '7e_annual_filer': tax_7e_filer,
                    '7e_annual_nonfiler': tax_7e_nonfiler,
                    'cgt_1yr_filer': cgt_1yr,
                    'cgt_2yr_filer': cgt_2yr,
                    'cgt_3yr_filer': cgt_3yr,
                    'cgt_4yr_plus_filer': cgt_4yr,
                    'wht_filer': wht_filer,
                    'wht_nonfiler': wht_nonfiler,
                    'stamp_duty_estimate': stamp_duty,
                },
                'rental_analysis': {
                    'estimated_monthly_rent': monthly_rent,
                    'annual_rental_income': annual_rental,
                    'gross_yield_pct': float(gross_yield),
                    'rental_tax_pct': 15.0,
                    'net_yield_pct': float(net_yield),
                },
                'loan_to_buy': {
                    'min_monthly_income_needed': min_income,
                    'estimated_emi': emi,
                    'down_payment_30pct': down_pmt,
                },
                'roi_projections': {
                    '1_year': roi_1yr,
                    '3_year': roi_3yr,
                    '5_year': roi_5yr,
                },
            },
            'legal_checklist': {
                'items': legal_items,
                'required_documents_buyer': docs_buyer,
                'required_documents_seller': docs_seller,
                'registration_authority': reg_authority,
            },
            'fraud_indicators': {
                'risk': fraud['risk'],
                'score': fraud['score'],
                'flags': fraud['flags'],
            },
            'role_insights': {
                'buyer': {
                    'summary': (
                        f"This {property_type} in {location}, {city} is priced at PKR {val:,}. "
                        f"True cost including taxes and fees is PKR {true_cost_total:,}."
                    ),
                    'action_items': buyer_action_items,
                    'negotiation_tip': negotiation_tip,
                },
                'seller': {
                    'summary': (
                        f"Your {property_type} in {location}, {city} is valued at PKR {val:,}. "
                        f"After WHT and fees, net-in-hand is PKR {net_seller:,}."
                    ),
                    'optimal_asking_price': f"PKR {fair_max:,} (based on {location} market rates)",
                    'best_time_to_sell': best_time_to_sell,
                    'action_items': seller_action_items,
                },
                'agent': {
                    'commission_pkr': agent_comm,
                    'pitch': (
                        f"{area_marla}M {property_type} in {location}, {city}. "
                        f"PKR {val:,}. Grade {grade} investment. {verdict}."
                        if area_marla
                        else f"{property_type} in {location}, {city}. "
                             f"PKR {val:,}. Grade {grade} investment. {verdict}."
                    ),
                    'deal_readiness': 'HIGH' if risk <= 3 else 'MEDIUM' if risk <= 6 else 'LOW',
                    'buyer_income_needed': f"Min PKR {min_income:,}/month for 70% financing",
                },
                'developer': {
                    'development_potential': dev_potential,
                    'permissible_floors': permissible_floors,
                    'estimated_rental_yield': f"{gross_yield:.1f}% gross / {net_yield:.1f}% net annually",
                    'investment_grade_note': grade_label,
                },
            },
            'recommendations': {
                'verdict': verdict,
                'top_3_actions': [
                    f"Get Fard from {reg_authority}",
                    'Verify seller CNIC matches all property documents',
                    third_action,
                ],
                'red_flags': red_flags,
                'green_flags': green_flags,
            },
        }
