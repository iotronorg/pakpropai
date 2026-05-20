from __future__ import annotations
import logging

from .calculators.base import MarketCalculator, TaxResult, LoanResult
from .calculators.pk import PakistanCalculator
from .calculators.ae import UAECalculator
from .calculators.gb import UKCalculator
from .calculators.us import USCalculator

logger = logging.getLogger(__name__)


def _unsupported_tax(country: str) -> TaxResult:
    return TaxResult(
        country=country, supported=False,
        advice=f"Tax calculation for '{country}' is not yet available.",
    )


def _unsupported_loan(country: str) -> LoanResult:
    return LoanResult(
        country=country, supported=False,
        reason=f"Loan eligibility for '{country}' is not yet available.",
    )


class FinancialEngine:
    """
    Registry + dispatcher for market-specific financial calculators.

    To add a new market: instantiate its calculator and call
    FinancialEngine.register(MyCalculator()) — nothing else changes.
    """

    _registry: dict[str, MarketCalculator] = {}

    @classmethod
    def register(cls, calc: MarketCalculator) -> None:
        cls._registry[calc.country.upper()] = calc

    @classmethod
    def _get(cls, country: str) -> MarketCalculator | None:
        return cls._registry.get(country.upper())

    @classmethod
    def calculate_tax(cls, country: str, fmv: int, **kwargs) -> TaxResult:
        calc = cls._get(country)
        if calc is None:
            logger.warning("FinancialEngine: no calculator for country=%s", country)
            return _unsupported_tax(country)
        try:
            return calc.calculate_tax(fmv, **kwargs)
        except Exception as exc:
            logger.error("FinancialEngine.calculate_tax country=%s error=%s", country, exc)
            return _unsupported_tax(country)

    @classmethod
    def check_loan(cls, country: str, monthly_income: int, loan_amount: int,
                   tenure_years: int = 20, **kwargs) -> LoanResult:
        calc = cls._get(country)
        if calc is None:
            logger.warning("FinancialEngine: no calculator for country=%s", country)
            return _unsupported_loan(country)
        try:
            return calc.check_loan(monthly_income, loan_amount, tenure_years, **kwargs)
        except Exception as exc:
            logger.error("FinancialEngine.check_loan country=%s error=%s", country, exc)
            return _unsupported_loan(country)


# Register all built-in calculators at module import time
FinancialEngine.register(PakistanCalculator())
FinancialEngine.register(UAECalculator())
FinancialEngine.register(UKCalculator())
FinancialEngine.register(USCalculator())
