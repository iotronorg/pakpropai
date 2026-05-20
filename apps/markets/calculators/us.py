from __future__ import annotations
from .base import MarketCalculator, TaxResult, LoanResult


class USCalculator(MarketCalculator):
    country = 'US'
    _MORTGAGE_RATE = 0.065

    def calculate_tax(self, fmv: int, annual_rate_pct: float = 1.2, **kwargs) -> TaxResult:
        annual_tax = int(fmv * (annual_rate_pct / 100))
        return TaxResult(
            country='US', supported=True,
            annual_tax=annual_tax,
            advice='Property tax rate is set by county — verify exact rate with local assessor.',
            notes=[
                f'Estimate based on {annual_rate_pct}% national-average annual property tax.',
                'Federal CGT on sale: 0%, 15%, or 20% depending on income and holding period.',
                'Primary residence exclusion: up to $250,000 gain ($500,000 married) if owned/lived ≥ 2 years.',
            ],
        )

    def check_loan(self, monthly_income: int, loan_amount: int,
                   tenure_years: int = 30, down_payment_pct: float = 0.20,
                   **kwargs) -> LoanResult:
        tenure_years = max(10, min(30, tenure_years))
        monthly_rate = self._MORTGAGE_RATE / 12
        n = tenure_years * 12
        emi = int(loan_amount * (monthly_rate * (1 + monthly_rate) ** n)
                  / ((1 + monthly_rate) ** n - 1))
        max_dti = int(monthly_income * 0.43)
        max_loan = int(max_dti * ((1 + monthly_rate) ** n - 1)
                       / (monthly_rate * (1 + monthly_rate) ** n))
        eligible = emi <= max_dti
        dep_pct_int = int(down_payment_pct * 100)
        reason = (
            f'Eligible — ~${emi:,}/month over {tenure_years} years at ~6.5% indicative rate.'
            if eligible
            else f'Monthly payment ${emi:,} exceeds 43% DTI limit. Max loan: ${max_loan:,}.'
        )
        return LoanResult(
            country='US', supported=True,
            eligible=eligible,
            monthly_emi=emi,
            max_affordable_loan=max_loan,
            annual_rate_pct=6.5,
            tenure_years=tenure_years,
            reason=reason,
            next_steps=[
                f'Minimum {dep_pct_int}% down payment typical (3% via FHA loan if qualified).',
                'Lenders: Chase, Wells Fargo, Bank of America, Rocket Mortgage.',
                'Required: W-2/tax returns (2 yrs), pay stubs, credit score ≥ 620.',
            ] if eligible else [],
        )
