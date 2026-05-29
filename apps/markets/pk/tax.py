from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class FBRTaxResult:
    country: str
    supported: bool
    wht_amount: int = 0
    cgt_amount: int = 0
    effective_rate: float = 0.0
    breakdown: dict = field(default_factory=dict)


class BaseTaxCalculator:
    country = ''
    supported = False

    def calculate_withholding_tax(self, amount: int, filer_status: str = 'filer') -> FBRTaxResult:
        return FBRTaxResult(country=self.country, supported=False)

    def calculate_capital_gains_tax(
        self, purchase_price: int, sale_price: int,
        holding_years: int, filer_status: str = 'filer',
    ) -> FBRTaxResult:
        return FBRTaxResult(country=self.country, supported=False)


class PakistanFBRCalculator(BaseTaxCalculator):
    """
    FBR Pakistan Section 236C WHT + Capital Gains Tax calculator.
    Rates sourced from SystemConfig keys (fbr_wht_filer_tier*_rate etc.)
    with hardcoded fallbacks — rate changes require config update only.
    """
    country = 'PK'
    supported = True

    # Section 236C WHT tiers: (upper_bound, rate)
    _WHT_FILER     = [(5_000_000, 0.01), (10_000_000, 0.02), (float('inf'), 0.03)]
    _WHT_NON_FILER = [(5_000_000, 0.02), (10_000_000, 0.04), (float('inf'), 0.06)]

    # CGT rates indexed by holding_years key → (filer_rate, non_filer_rate)
    _CGT = {1: (0.15, 0.30), 2: (0.125, 0.25)}
    _CGT_DEFAULT = (0.10, 0.20)  # year 3+

    def _wht_rate(self, amount: int, filer_status: str) -> float:
        tiers = self._WHT_FILER if filer_status == 'filer' else self._WHT_NON_FILER
        try:
            from apps.config.services import SystemConfigService
            prefix = f"fbr_wht_{'filer' if filer_status == 'filer' else 'non_filer'}"
            t1 = SystemConfigService.get(f'{prefix}_tier1_rate')
            t2 = SystemConfigService.get(f'{prefix}_tier2_rate')
            t3 = SystemConfigService.get(f'{prefix}_tier3_rate')
            if t1 and t2 and t3:
                tiers = [
                    (5_000_000, float(t1)),
                    (10_000_000, float(t2)),
                    (float('inf'), float(t3)),
                ]
        except Exception:
            pass
        for cap, rate in tiers:
            if amount <= cap:
                return rate
        return tiers[-1][1]

    def calculate_withholding_tax(self, amount: int, filer_status: str = 'filer') -> FBRTaxResult:
        rate = self._wht_rate(amount, filer_status)
        return FBRTaxResult(
            country='PK', supported=True,
            wht_amount=int(amount * rate),
            cgt_amount=0,
            effective_rate=rate,
            breakdown={
                'section': '236C', 'filer_status': filer_status,
                'rate': rate, 'amount': amount,
            },
        )

    def calculate_capital_gains_tax(
        self, purchase_price: int, sale_price: int,
        holding_years: int, filer_status: str = 'filer',
    ) -> FBRTaxResult:
        gain = max(0, sale_price - purchase_price)
        if gain == 0:
            return FBRTaxResult(
                country='PK', supported=True,
                breakdown={'gain': 0, 'holding_years': holding_years},
            )

        filer_idx = 0 if filer_status == 'filer' else 1
        base_rates = self._CGT.get(holding_years, self._CGT_DEFAULT)
        rate = base_rates[filer_idx]

        try:
            from apps.config.services import SystemConfigService
            yr = min(holding_years, 3)
            suffix = 'filer' if filer_status == 'filer' else 'non_filer'
            cfg = SystemConfigService.get(f'fbr_cgt_yr{yr}_{suffix}_rate')
            if cfg:
                rate = float(cfg)
        except Exception:
            pass

        return FBRTaxResult(
            country='PK', supported=True,
            wht_amount=0,
            cgt_amount=int(gain * rate),
            effective_rate=rate,
            breakdown={
                'purchase_price': purchase_price, 'sale_price': sale_price,
                'gain': gain, 'holding_years': holding_years,
                'filer_status': filer_status, 'rate': rate,
            },
        )
