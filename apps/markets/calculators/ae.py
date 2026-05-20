from __future__ import annotations
from .base import MarketCalculator, TaxResult, LoanResult


class UAECalculator(MarketCalculator):
    country = 'AE'

    _DLD_RATE = 0.04          # Dubai Land Department transfer fee
    _TRUSTEE_FEE_AED = 4_000  # Registration trustee fee (flat per transaction)
    _MORTGAGE_RATE = 0.04     # Indicative UAE bank mortgage rate

    def calculate_tax(self, fmv: int, is_purchase: bool = True, **kwargs) -> TaxResult:
        transfer_tax = int(fmv * self._DLD_RATE) + self._TRUSTEE_FEE_AED if is_purchase else 0
        notes = (
            ['UAE has no income tax, capital gains tax, or inheritance tax on property.',
             'DLD Transfer Fee: 4% of purchase price + AED 4,000 registration fee.']
            if is_purchase
            else ['No transfer tax applies on sale — only the buyer pays the DLD fee.']
        )
        return TaxResult(
            country='AE', supported=True,
            transfer_tax=transfer_tax,
            total_on_sale=0,
            advice='Budget 4% of purchase price for DLD fees at time of transfer.',
            notes=notes,
        )

    def check_loan(self, monthly_income: int, loan_amount: int,
                   tenure_years: int = 25, down_payment_pct: float = 0.20,
                   **kwargs) -> LoanResult:
        tenure_years = max(5, min(25, tenure_years))
        monthly_rate = self._MORTGAGE_RATE / 12
        n = tenure_years * 12
        emi = int(loan_amount * (monthly_rate * (1 + monthly_rate) ** n)
                  / ((1 + monthly_rate) ** n - 1))
        max_emi = int(monthly_income * 0.50)
        max_loan = int(max_emi * ((1 + monthly_rate) ** n - 1)
                       / (monthly_rate * (1 + monthly_rate) ** n))
        eligible = emi <= max_emi
        dep_pct_int = int(down_payment_pct * 100)
        reason = (
            f'Eligible — EMI AED {emi:,}/month over {tenure_years} years at 4% p.a.'
            if eligible
            else f'EMI AED {emi:,}/month exceeds 50% income limit. Max loan: AED {max_loan:,}.'
        )
        return LoanResult(
            country='AE', supported=True,
            eligible=eligible,
            monthly_emi=emi,
            max_affordable_loan=max_loan,
            annual_rate_pct=4.0,
            tenure_years=tenure_years,
            reason=reason,
            next_steps=[
                f'Minimum {dep_pct_int}% down payment required (non-residents: 40-50%).',
                'Lenders: Emirates NBD, ADCB, Dubai Islamic Bank, Mashreq, FAB.',
                'Required: passport, 6 months bank statements, salary certificate/trade licence.',
            ] if eligible else [],
        )
