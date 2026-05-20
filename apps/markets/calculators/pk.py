from __future__ import annotations
from .base import MarketCalculator, TaxResult, LoanResult


class PakistanCalculator(MarketCalculator):
    country = 'PK'

    def calculate_tax(self, fmv: int, filer_status: str = 'filer',
                      properties_count: int = 1, is_self_occupied: bool = False,
                      **kwargs) -> TaxResult:
        THRESHOLD = 25_000_000
        notes: list[str] = []

        if is_self_occupied and properties_count == 1:
            wht = int(fmv * (0.03 if filer_status == 'filer' else 0.06))
            return TaxResult(
                country='PK', supported=True,
                annual_tax=0, exempt=True,
                exemption_reason='Single self-occupied residential house — fully exempt from Section 7E.',
                withholding_tax=wht,
                total_on_sale=wht,
                advice='Your property is exempt from 7E. Withholding tax applies if you sell.',
                notes=['Self-occupied exemption covers ONE house only.'],
            )

        if fmv <= THRESHOLD:
            annual_tax = 0
            notes.append(f'FMV PKR {fmv:,} is below the PKR 25M threshold — no 7E liability.')
        else:
            rate = 0.01 if filer_status == 'filer' else 0.02
            annual_tax = int(fmv * rate)
            notes.append(f'Rate: {"1%" if filer_status == "filer" else "2% (non-filer)"} × PKR {fmv:,}')

        wht_rate = (0.02 if fmv <= 5_000_000 else 0.03) if filer_status == 'filer' \
                   else (0.04 if fmv <= 5_000_000 else 0.06)
        wht = int(fmv * wht_rate)
        stamp = int(fmv * (0.03 if filer_status == 'filer' else 0.05))
        advice = (
            'Becoming an active FBR filer can halve your tax rates. '
            'Register at the FBR IRIS portal (iris.fbr.gov.pk).'
            if filer_status == 'non_filer'
            else f'File annual income tax return by Sept 30 and declare this property at FMV PKR {fmv:,}.'
        )
        return TaxResult(
            country='PK', supported=True,
            annual_tax=annual_tax,
            withholding_tax=wht,
            stamp_duty=stamp,
            total_on_sale=wht + stamp,
            exempt=(annual_tax == 0),
            exemption_reason='Below PKR 25M threshold' if fmv <= THRESHOLD else '',
            advice=advice, notes=notes,
        )

    def check_loan(self, monthly_income: int, loan_amount: int,
                   tenure_years: int = 20, existing_emi: int = 0,
                   scheme: str = 'conventional', **kwargs) -> LoanResult:
        tenure_years = max(5, min(25, tenure_years))
        rate = 0.07 if scheme == 'apna_ghar' else 0.22
        monthly_rate = rate / 12
        n = tenure_years * 12

        emi = int(loan_amount * (monthly_rate * (1 + monthly_rate) ** n)
                  / ((1 + monthly_rate) ** n - 1)) if monthly_rate > 0 else loan_amount // n
        max_emi = max(0, int(monthly_income * 0.5) - existing_emi)
        max_loan = int(max_emi * ((1 + monthly_rate) ** n - 1)
                       / (monthly_rate * (1 + monthly_rate) ** n)) \
            if monthly_rate > 0 and max_emi > 0 else max_emi * n

        eligible = emi <= max_emi and monthly_income >= 25_000
        apna_ghar_ok = (scheme == 'apna_ghar'
                        and 25_000 <= monthly_income <= 200_000
                        and loan_amount <= 10_000_000)

        if not eligible:
            reason = (
                'Monthly income below PKR 25,000 minimum.'
                if monthly_income < 25_000
                else f'EMI PKR {emi:,}/month exceeds 50% income limit. Max loan: PKR {max_loan:,}.'
            )
            next_steps: list[str] = []
        else:
            reason = f'Eligible — EMI PKR {emi:,}/month over {tenure_years} years.'
            next_steps = (
                ['Apply at: HBL, UBL, Meezan Bank, Bank Alfalah, NBP, MCB, Bank of Punjab',
                 'Documents: CNIC, 6 months bank statements, salary slip or business proof',
                 'Also apply via NAPHDA portal at pmrc.com.pk']
                if apna_ghar_ok
                else ['Compare rates at HBL, UBL, MCB, Bank Alfalah',
                      'Prepare: 6 months bank statements, CNIC, salary slip/NTN',
                      'Minimum 30% down payment required']
            )
        return LoanResult(
            country='PK', supported=True,
            eligible=eligible,
            monthly_emi=emi,
            max_affordable_loan=max_loan,
            annual_rate_pct=round(rate * 100, 1),
            tenure_years=tenure_years,
            reason=reason,
            next_steps=next_steps,
            raw={'scheme': scheme, 'apna_ghar_eligible': apna_ghar_ok},
        )
