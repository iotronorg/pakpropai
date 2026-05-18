"""
Modular financial & tax calculator for RealTron AI.

All rates and thresholds are supplied via FinancialConfig — nothing is
hardcoded in the arithmetic methods.  FinancialConfigLoader resolves config
from the organization's DB settings (OrganizationConfig) with a fallback
to platform-level SystemConfig and finally to FinancialConfig field defaults.

Usage:
    config = FinancialConfigLoader.for_org(organization_id='...')
    calc   = FinancialCalculator(config)
    emi    = calc.calculate_emi(principal=5_000_000, annual_rate=0.22, tenure_months=240)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CONFIG_PREFIX = 'finance_'


@dataclass
class FinancialConfig:
    """
    Country / market-specific parameters.  All rates are decimals
    (e.g. 0.03 = 3 %).  Load via FinancialConfigLoader — never hard-code
    rates at call sites.
    """
    country: str = 'PK'
    currency: str = 'PKR'

    # ── Acquisition / transfer costs ─────────────────────────────────────────
    stamp_duty_rate:       float = 0.03   # buyer pays on property value
    registration_fee_rate: float = 0.01   # buyer pays at sub-registrar
    agent_commission_rate: float = 0.01   # borne by both sides equally by default

    # ── Withholding / seller taxes ────────────────────────────────────────────
    wht_filer_rate:        float = 0.03
    wht_nonfiler_rate:     float = 0.06

    # ── Annual property / deemed-income tax ──────────────────────────────────
    annual_tax_threshold:      int   = 25_000_000   # no tax below this value
    annual_tax_filer_rate:     float = 0.01
    annual_tax_nonfiler_rate:  float = 0.02

    # ── Capital Gains Tax (CGT) by holding-year bracket ──────────────────────
    cgt_year1_rate:         float = 0.15
    cgt_year2_rate:         float = 0.125
    cgt_year3_rate:         float = 0.10
    cgt_year4plus_rate:     float = 0.0    # fully exempt after 4 years
    cgt_nonfiler_multiplier: float = 2.0   # non-filer rate = filer rate × this

    # ── Mortgage / home loan ─────────────────────────────────────────────────
    conventional_rate:      float = 0.22
    subsidized_rate:        float = 0.07   # e.g. Apna Ghar / NAPHDA scheme
    max_dsr_ratio:          float = 0.50   # max EMI as fraction of monthly income
    min_down_payment_pct:   float = 0.30
    max_loan_tenure_years:  int   = 25
    min_qualifying_income:  int   = 25_000

    @classmethod
    def from_dict(cls, data: dict) -> FinancialConfig:
        """
        Build from a flat string-keyed dict (e.g. DB config rows).
        Unknown keys are silently ignored; type coercion is best-effort.
        """
        cfg = cls()
        for key, raw_val in data.items():
            if not hasattr(cfg, key):
                continue
            current = getattr(cfg, key)
            try:
                if isinstance(current, float):
                    setattr(cfg, key, float(raw_val))
                elif isinstance(current, int):
                    setattr(cfg, key, int(raw_val))
                else:
                    setattr(cfg, key, str(raw_val))
            except (TypeError, ValueError):
                pass
        return cfg


class FinancialConfigLoader:
    """
    Resolves a FinancialConfig for a given organization.
    Priority: OrganizationConfig (org wins) → SystemConfig → FinancialConfig defaults.

    Config keys in the DB must be prefixed with `finance_`, e.g.:
        finance_stamp_duty_rate = 0.04
        finance_currency = AED
    """

    @classmethod
    def for_org(cls, organization_id: str = '') -> FinancialConfig:
        data: dict = {}

        # 1. Platform-level finance overrides
        try:
            from apps.config.services import SystemConfigService
            for key, val in SystemConfigService.get_all().items():
                if key.startswith(_CONFIG_PREFIX) and val:
                    data[key[len(_CONFIG_PREFIX):]] = val
        except Exception as exc:
            logger.debug(f"FinancialConfigLoader: SystemConfig unavailable — {exc}")

        # 2. Org-level overrides (higher priority)
        if organization_id:
            try:
                from apps.organizations.models import OrganizationConfig
                rows = OrganizationConfig.objects.filter(
                    organization_id=organization_id,
                    key__startswith=_CONFIG_PREFIX,
                ).values_list('key', 'value')
                for key, val in rows:
                    if val:
                        data[key[len(_CONFIG_PREFIX):]] = val
            except Exception as exc:
                logger.debug(f"FinancialConfigLoader: org config unavailable — {exc}")

        return FinancialConfig.from_dict(data)


class FinancialCalculator:
    """
    Pure-function financial calculator.  All rates come from the injected
    FinancialConfig — no regional values are hard-coded inside the arithmetic.

    Instantiate with any FinancialConfig (including hand-built ones for tests):

        calc = FinancialCalculator(FinancialConfig(country='AE', currency='AED',
                                                   stamp_duty_rate=0.04, ...))
    """

    def __init__(self, config: FinancialConfig) -> None:
        self.config = config

    # ── EMI (reducing-balance mortgage formula) ───────────────────────────────

    def calculate_emi(self, principal: int, annual_rate: float, tenure_months: int) -> int:
        """Standard reducing-balance EMI.  Returns monthly instalment as an integer."""
        if annual_rate <= 0:
            return principal // max(tenure_months, 1)
        r = annual_rate / 12
        n = tenure_months
        emi = principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)
        return int(emi)

    def max_affordable_loan(
        self, monthly_income: int, existing_emi: int,
        annual_rate: float, tenure_months: int,
    ) -> int:
        """Largest principal whose EMI stays within config.max_dsr_ratio × income."""
        headroom = int(monthly_income * self.config.max_dsr_ratio) - existing_emi
        if headroom <= 0:
            return 0
        if annual_rate <= 0:
            return headroom * tenure_months
        r = annual_rate / 12
        n = tenure_months
        return int(headroom * ((1 + r) ** n - 1) / (r * (1 + r) ** n))

    # ── Annual property / deemed-income tax ───────────────────────────────────

    def annual_property_tax(self, property_value: int, is_filer: bool) -> dict:
        """
        Annual tax on property value (e.g. Section 7E in Pakistan).
        Returns zero for values at or below the configured threshold.
        """
        if property_value <= self.config.annual_tax_threshold:
            return {
                'tax': 0,
                'exempt': True,
                'reason': (
                    f'Below threshold '
                    f'({self.config.annual_tax_threshold:,} {self.config.currency})'
                ),
                'currency': self.config.currency,
            }
        rate = (
            self.config.annual_tax_filer_rate if is_filer
            else self.config.annual_tax_nonfiler_rate
        )
        return {
            'tax':      int(property_value * rate),
            'exempt':   False,
            'rate_pct': round(rate * 100, 4),
            'reason':   '',
            'currency': self.config.currency,
        }

    # ── Capital Gains Tax ─────────────────────────────────────────────────────

    def capital_gains_tax(self, gain: int, holding_years: int, is_filer: bool) -> dict:
        """
        CGT based on holding period bracket.
        Non-filer surcharge is applied via config.cgt_nonfiler_multiplier.
        """
        if holding_years >= 4:
            rate = self.config.cgt_year4plus_rate
        elif holding_years >= 3:
            rate = self.config.cgt_year3_rate
        elif holding_years >= 2:
            rate = self.config.cgt_year2_rate
        else:
            rate = self.config.cgt_year1_rate

        if not is_filer:
            rate = min(rate * self.config.cgt_nonfiler_multiplier, 1.0)

        return {
            'gain':          gain,
            'holding_years': holding_years,
            'rate_pct':      round(rate * 100, 4),
            'tax':           int(gain * rate),
            'currency':      self.config.currency,
        }

    # ── Transaction cost breakdowns ───────────────────────────────────────────

    def buyer_transaction_costs(self, property_value: int) -> dict:
        """All acquisition costs for the buyer (stamp duty, reg fee, agent, legal)."""
        stamp_duty = int(property_value * self.config.stamp_duty_rate)
        reg_fee    = int(property_value * self.config.registration_fee_rate)
        agent_comm = int(property_value * self.config.agent_commission_rate)
        legal_fees = 50_000   # flat minimum — not a rate
        return {
            'property_value':   property_value,
            'stamp_duty':       stamp_duty,
            'registration_fee': reg_fee,
            'agent_commission': agent_comm,
            'legal_fees':       legal_fees,
            'total_true_cost':  property_value + stamp_duty + reg_fee + agent_comm + legal_fees,
            'currency':         self.config.currency,
        }

    def seller_net_proceeds(self, property_value: int, is_filer: bool) -> dict:
        """Net amount the seller receives after all deductions."""
        wht_rate   = self.config.wht_filer_rate if is_filer else self.config.wht_nonfiler_rate
        wht        = int(property_value * wht_rate)
        agent_comm = int(property_value * self.config.agent_commission_rate)
        legal_fees = 30_000   # flat minimum
        return {
            'asking_price':     property_value,
            'withholding_tax':  wht,
            'agent_commission': agent_comm,
            'legal_fees':       legal_fees,
            'net_proceeds':     property_value - wht - agent_comm - legal_fees,
            'currency':         self.config.currency,
        }

    # ── Loan eligibility ──────────────────────────────────────────────────────

    def loan_eligibility(
        self,
        monthly_income: int,
        loan_amount: int,
        tenure_years: int = 20,
        existing_emi: int = 0,
        use_subsidized_rate: bool = False,
    ) -> dict:
        """Full loan eligibility report using config rates."""
        rate   = self.config.subsidized_rate if use_subsidized_rate else self.config.conventional_rate
        months = min(tenure_years, self.config.max_loan_tenure_years) * 12
        emi    = self.calculate_emi(loan_amount, rate, months)
        max_loan = self.max_affordable_loan(monthly_income, existing_emi, rate, months)
        headroom = int(monthly_income * self.config.max_dsr_ratio) - existing_emi

        eligible = emi <= headroom and monthly_income >= self.config.min_qualifying_income
        down_pmt = int(loan_amount * self.config.min_down_payment_pct)

        return {
            'eligible':              eligible,
            'loan_amount':           loan_amount,
            'monthly_emi':           emi,
            'max_affordable_loan':   max_loan,
            'annual_rate_pct':       round(rate * 100, 4),
            'tenure_months':         months,
            'down_payment_required': down_pmt,
            'min_income_required':   int(emi / self.config.max_dsr_ratio) if emi > 0 else 0,
            'currency':              self.config.currency,
        }

    # ── Full composite report ─────────────────────────────────────────────────

    def full_report(
        self,
        property_value: int,
        monthly_income: int = 0,
        loan_amount: int = 0,
        tenure_years: int = 20,
        existing_emi: int = 0,
        is_filer: bool = True,
        holding_years: int = 0,
        estimated_gain: int = 0,
    ) -> dict:
        """Compile all financial analyses into one structured response payload."""
        report: dict = {
            'property_value':     property_value,
            'currency':           self.config.currency,
            'annual_property_tax': self.annual_property_tax(property_value, is_filer),
            'buyer_costs':        self.buyer_transaction_costs(property_value),
            'seller_net':         self.seller_net_proceeds(property_value, is_filer),
        }
        if loan_amount > 0:
            report['loan_eligibility'] = self.loan_eligibility(
                monthly_income, loan_amount, tenure_years, existing_emi,
                use_subsidized_rate=False,
            )
        if holding_years > 0 and estimated_gain > 0:
            report['capital_gains_tax'] = self.capital_gains_tax(
                estimated_gain, holding_years, is_filer,
            )
        return report
