import logging
from apps.markets.engine import FinancialEngine

logger = logging.getLogger(__name__)


def calculate_7e_tax(
    fmv_pkr: int,
    filer_status: str,
    properties_count: int = 1,
    is_self_occupied: bool = False,
    country: str = 'PK',
) -> dict:
    """
    Calculate property tax for a given country.
    Call this when user asks about annual property tax, Section 7E, FBR tax,
    filer vs non-filer rates, DLD fees (UAE), SDLT (UK), or property tax (US).

    Args:
        fmv_pkr: Fair Market Value in local currency
        filer_status: 'filer' or 'non_filer' (PK only; ignored for other markets)
        properties_count: Number of properties owned (PK only)
        is_self_occupied: True if primary residence (PK only)
        country: ISO 3166-1 alpha-2 code (default 'PK')
    """
    result = FinancialEngine.calculate_tax(
        country,
        fmv=fmv_pkr,
        filer_status=filer_status,
        properties_count=properties_count,
        is_self_occupied=is_self_occupied,
    )
    if not result.supported:
        return {'supported': False, 'country': country, 'message': result.advice}
    return {
        'country':                       result.country,
        'supported':                     True,
        'fmv':                           fmv_pkr,
        'annual_tax':                    result.annual_tax,
        'transfer_tax':                  result.transfer_tax,
        'stamp_duty':                    result.stamp_duty,
        'withholding_tax':               result.withholding_tax,
        'total_cost_on_sale':            result.total_on_sale,
        'exempt':                        result.exempt,
        'exemption_reason':              result.exemption_reason,
        'advice':                        result.advice,
        'notes':                         result.notes,
        # PK backward-compat aliases (existing callers that read *_pkr keys)
        'fmv_pkr':                       fmv_pkr,
        'tax_7e_annual_pkr':             result.annual_tax,
        'withholding_tax_on_sale_pkr':   result.withholding_tax,
        'stamp_duty_estimate_pkr':       result.stamp_duty,
        'total_cost_if_selling_pkr':     result.total_on_sale,
    }


def check_loan_eligibility(
    monthly_income_pkr: int,
    loan_amount_pkr: int,
    tenure_years: int = 20,
    existing_emi_pkr: int = 0,
    scheme: str = 'conventional',
    country: str = 'PK',
) -> dict:
    """
    Check home loan / mortgage eligibility for any supported market.
    Call this when user asks about loan, mortgage, home financing, EMI,
    Apna Ghar scheme (PK), or equivalent financing in their country.

    Args:
        monthly_income_pkr: Net monthly income in local currency
        loan_amount_pkr: Required loan amount in local currency
        tenure_years: Repayment period in years
        existing_emi_pkr: Existing monthly obligations (PK only)
        scheme: 'apna_ghar' for Pakistan subsidized scheme, 'conventional' otherwise
        country: ISO 3166-1 alpha-2 code (default 'PK')
    """
    result = FinancialEngine.check_loan(
        country,
        monthly_income=monthly_income_pkr,
        loan_amount=loan_amount_pkr,
        tenure_years=tenure_years,
        existing_emi=existing_emi_pkr,
        scheme=scheme,
    )
    if not result.supported:
        return {'supported': False, 'country': country, 'message': result.reason}
    return {
        'country':                      result.country,
        'supported':                    True,
        'eligible':                     result.eligible,
        'monthly_income':               monthly_income_pkr,
        'loan_requested':               loan_amount_pkr,
        'estimated_monthly_emi':        result.monthly_emi,
        'max_affordable_loan':          result.max_affordable_loan,
        'annual_interest_rate_percent': result.annual_rate_pct,
        'tenure_years':                 result.tenure_years,
        'reason':                       result.reason,
        'next_steps':                   result.next_steps,
        # PK backward-compat aliases
        'monthly_income_pkr':           monthly_income_pkr,
        'loan_requested_pkr':           loan_amount_pkr,
        'estimated_monthly_emi_pkr':    result.monthly_emi,
        'max_affordable_loan_pkr':      result.max_affordable_loan,
        'scheme':                       scheme,
    }
