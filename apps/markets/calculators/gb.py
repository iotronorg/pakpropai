from __future__ import annotations
from .base import MarketCalculator, TaxResult, LoanResult

# SDLT bands — England & N. Ireland, standard residential (2024 rates)
_SDLT_BANDS = [
    (250_000,       0.00),
    (925_000,       0.05),
    (1_500_000,     0.10),
    (float('inf'), 0.12),
]
_SDLT_FIRST_TIME_THRESHOLD = 425_000
_SDLT_FIRST_TIME_RATE_ABOVE = 0.05


def _calc_sdlt(price: int, buyer_type: str) -> int:
    if buyer_type == 'first_time' and price <= 625_000:
        taxable = max(0, price - _SDLT_FIRST_TIME_THRESHOLD)
        return int(taxable * _SDLT_FIRST_TIME_RATE_ABOVE)
    surcharge = int(price * 0.03) if buyer_type == 'additional' else 0
    tax = 0
    prev = 0
    for band, rate in _SDLT_BANDS:
        if price <= prev:
            break
        taxable = min(price, band) - prev
        tax += int(taxable * rate)
        prev = band
    return tax + surcharge


class UKCalculator(MarketCalculator):
    country = 'GB'
    _MORTGAGE_RATE = 0.045

    def calculate_tax(self, fmv: int, buyer_type: str = 'standard', **kwargs) -> TaxResult:
        sdlt = _calc_sdlt(fmv, buyer_type)
        notes = [
            f'SDLT (buyer_type={buyer_type}): £{sdlt:,}',
            'CGT on gain: 18% (basic rate) or 24% (higher rate). Annual exemption: £3,000.',
            'Annual Council Tax payable by occupier — set by local authority.',
        ]
        if buyer_type == 'additional':
            notes.insert(0, 'Additional 3% SDLT surcharge applies for second/buy-to-let properties.')
        return TaxResult(
            country='GB', supported=True,
            transfer_tax=sdlt,
            total_on_sale=0,
            advice='Consult a UK conveyancer for SDLT relief eligibility and CGT liability.',
            notes=notes,
        )

    def check_loan(self, monthly_income: int, loan_amount: int,
                   tenure_years: int = 25, deposit_pct: float = 0.10,
                   **kwargs) -> LoanResult:
        tenure_years = max(5, min(35, tenure_years))
        monthly_rate = self._MORTGAGE_RATE / 12
        n = tenure_years * 12
        emi = int(loan_amount * (monthly_rate * (1 + monthly_rate) ** n)
                  / ((1 + monthly_rate) ** n - 1))
        max_emi = int(monthly_income * 0.45)
        max_loan = int(max_emi * ((1 + monthly_rate) ** n - 1)
                       / (monthly_rate * (1 + monthly_rate) ** n))
        eligible = emi <= max_emi
        dep_pct_int = int(deposit_pct * 100)
        reason = (
            f'Eligible — approx. £{emi:,}/month over {tenure_years} years at 4.5% indicative rate.'
            if eligible
            else f'Monthly repayment £{emi:,} exceeds 45% income limit. Max loan: £{max_loan:,}.'
        )
        return LoanResult(
            country='GB', supported=True,
            eligible=eligible,
            monthly_emi=emi,
            max_affordable_loan=max_loan,
            annual_rate_pct=4.5,
            tenure_years=tenure_years,
            reason=reason,
            next_steps=[
                f'Minimum {dep_pct_int}% deposit required (5% via Help to Buy schemes).',
                'Lenders: Nationwide, HSBC, Barclays, Santander, Halifax.',
                'Required: 3 months payslips, P60, bank statements, ID.',
            ] if eligible else [],
        )
