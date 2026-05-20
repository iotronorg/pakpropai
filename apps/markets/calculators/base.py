from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TaxResult:
    country:           str
    supported:         bool
    annual_tax:        int  = 0
    transfer_tax:      int  = 0
    stamp_duty:        int  = 0
    withholding_tax:   int  = 0
    total_on_sale:     int  = 0
    exempt:            bool = False
    exemption_reason:  str  = ''
    advice:            str  = ''
    notes:             list = field(default_factory=list)
    raw:               dict = field(default_factory=dict)


@dataclass
class LoanResult:
    country:             str
    supported:           bool
    eligible:            bool  = False
    monthly_emi:         int   = 0
    max_affordable_loan: int   = 0
    annual_rate_pct:     float = 0.0
    tenure_years:        int   = 0
    reason:              str   = ''
    next_steps:          list  = field(default_factory=list)
    raw:                 dict  = field(default_factory=dict)


class MarketCalculator(ABC):
    """One implementation per country. Registered in FinancialEngine."""

    country: str  # ISO 3166-1 alpha-2 — must match MARKET_REGISTRY key

    @abstractmethod
    def calculate_tax(self, fmv: int, **kwargs) -> TaxResult:
        """
        kwargs vary by market:
          PK: filer_status, properties_count, is_self_occupied
          AE: is_purchase (bool)
          GB: buyer_type ('first_time'|'additional'|'standard')
          US: annual_rate_pct (float, default 1.2)
        """

    @abstractmethod
    def check_loan(self, monthly_income: int, loan_amount: int,
                   tenure_years: int = 20, **kwargs) -> LoanResult:
        """
        kwargs vary by market:
          PK: existing_emi, scheme ('conventional'|'apna_ghar')
          AE: down_payment_pct (float, default 0.20)
          GB: deposit_pct (float, default 0.10)
          US: down_payment_pct (float, default 0.20)
        """
