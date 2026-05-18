import logging

logger = logging.getLogger(__name__)


def calculate_7e_tax(
    fmv_pkr: int,
    filer_status: str,
    properties_count: int = 1,
    is_self_occupied: bool = False,
    country: str = 'PK',
) -> dict:
    """
    Calculate property tax for a given country. Currently supports Pakistan (Section 7E CVT).
    Call this when user asks about annual property tax, Section 7E, FBR tax on property,
    filer vs non-filer rates, or how much tax they owe on their property.

    Args:
        fmv_pkr: Fair Market Value of the property in the local currency (e.g. 30000000 for PKR 3 crore)
        filer_status: Must be 'filer' or 'non_filer'. Ask user if unknown.
        properties_count: Total number of properties the person owns (default 1)
        is_self_occupied: True if this is the person's primary/only home they live in
        country: ISO 3166-1 alpha-2 country code (default 'PK'). Other markets not yet supported.
    """
    if country != 'PK':
        return {
            'supported': False,
            'country': country,
            'message': f"Property tax calculation for '{country}' is not yet available. Currently only Pakistan (PK) is supported.",
        }
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

        wht_rate = (0.02 if fmv_pkr <= 5_000_000 else 0.03) if filer_status == 'filer' else (0.04 if fmv_pkr <= 5_000_000 else 0.06)
        wht = int(fmv_pkr * wht_rate)
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


def check_loan_eligibility(
    monthly_income_pkr: int,
    loan_amount_pkr: int,
    tenure_years: int = 20,
    existing_emi_pkr: int = 0,
    scheme: str = 'conventional',
    country: str = 'PK',
) -> dict:
    """
    Check home loan / mortgage eligibility. Currently supports Pakistan bank rules and Apna Ghar scheme.
    Call this when user asks about loan, mortgage, home financing, Apna Ghar scheme, or EMI.

    Args:
        monthly_income_pkr: Net monthly take-home income in local currency
        loan_amount_pkr: Required loan amount in local currency
        tenure_years: Repayment period in years (5 to 25)
        existing_emi_pkr: Existing monthly EMI/loan obligations (default 0)
        scheme: 'apna_ghar' for Pakistan subsidized scheme, 'conventional' for market rate banks
        country: ISO 3166-1 alpha-2 country code (default 'PK'). Other markets not yet supported.
    """
    if country != 'PK':
        return {
            'supported': False,
            'country': country,
            'message': f"Loan eligibility calculation for '{country}' is not yet available. Currently only Pakistan (PK) is supported.",
        }
    try:
        tenure_years = max(5, min(25, tenure_years))
        RATE = 0.07 if scheme == 'apna_ghar' else 0.22
        monthly_rate = RATE / 12
        n = tenure_years * 12

        if monthly_rate > 0:
            emi = int(loan_amount_pkr * (monthly_rate * (1 + monthly_rate)**n) / ((1 + monthly_rate)**n - 1))
        else:
            emi = loan_amount_pkr // n

        max_emi_allowed = max(0, int(monthly_income_pkr * 0.5) - existing_emi_pkr)
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
            reason = (
                'Monthly income below minimum requirement of PKR 25,000 for any home loan.'
                if monthly_income_pkr < 25_000
                else f'EMI of PKR {emi:,}/month exceeds 50% income limit (PKR {max_emi_allowed:,}/month). Max loan: PKR {max_loan:,}.'
            )
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
