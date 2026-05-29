"""
Tests for PakistanFBRCalculator + get_tax_calculator registry (10 tests).

Coverage:
  a  filer WHT tier 1 (amount ≤ 5M)
  b  filer WHT tier 2 (5M < amount ≤ 10M)
  c  filer WHT tier 3 (amount > 10M)
  d  non-filer WHT tiers
  e  CGT year 1 filer
  f  CGT year 1 non-filer
  g  CGT year 3+ filer
  h  CGT year 3+ non-filer
  i  zero gain → zero CGT
  j  registry returns PK calculator for PK
  k  registry returns unsupported for AE
  l  SystemConfig FBR key overrides hardcoded fallback
"""

from unittest.mock import patch
from django.test import TestCase

from apps.markets.pk.tax import PakistanFBRCalculator, BaseTaxCalculator
from apps.markets.registry import get_tax_calculator


class WHTFilerTiersTest(TestCase):
    """a–c — filer WHT tiers."""

    def _calc(self, amount):
        with patch('apps.config.services.SystemConfigService.get', return_value=None):
            return PakistanFBRCalculator().calculate_withholding_tax(amount, 'filer')

    def test_a_filer_tier1_rate(self):
        result = self._calc(3_000_000)
        # Rate = 1% for ≤ 5M
        self.assertEqual(result.wht_amount, int(3_000_000 * 0.01))
        self.assertAlmostEqual(result.effective_rate, 0.01)

    def test_b_filer_tier2_rate(self):
        result = self._calc(7_000_000)
        self.assertEqual(result.wht_amount, int(7_000_000 * 0.02))
        self.assertAlmostEqual(result.effective_rate, 0.02)

    def test_c_filer_tier3_rate(self):
        result = self._calc(15_000_000)
        self.assertEqual(result.wht_amount, int(15_000_000 * 0.03))
        self.assertAlmostEqual(result.effective_rate, 0.03)


class WHTNonFilerTiersTest(TestCase):
    """d — non-filer WHT tiers."""

    def _calc(self, amount):
        with patch('apps.config.services.SystemConfigService.get', return_value=None):
            return PakistanFBRCalculator().calculate_withholding_tax(amount, 'non_filer')

    def test_d_non_filer_tier1(self):
        r = self._calc(2_000_000)
        self.assertEqual(r.wht_amount, int(2_000_000 * 0.02))

    def test_d_non_filer_tier3(self):
        r = self._calc(15_000_000)
        self.assertEqual(r.wht_amount, int(15_000_000 * 0.06))


class CGTFilerTest(TestCase):
    """e — CGT year 1 filer = 15%."""

    def test_e_cgt_year1_filer(self):
        with patch('apps.config.services.SystemConfigService.get', return_value=None):
            r = PakistanFBRCalculator().calculate_capital_gains_tax(
                purchase_price=5_000_000, sale_price=8_000_000,
                holding_years=1, filer_status='filer',
            )
        gain = 3_000_000
        self.assertEqual(r.cgt_amount, int(gain * 0.15))
        self.assertAlmostEqual(r.effective_rate, 0.15)


class CGTNonFilerTest(TestCase):
    """f — CGT year 1 non-filer = 30%."""

    def test_f_cgt_year1_non_filer(self):
        with patch('apps.config.services.SystemConfigService.get', return_value=None):
            r = PakistanFBRCalculator().calculate_capital_gains_tax(
                purchase_price=5_000_000, sale_price=8_000_000,
                holding_years=1, filer_status='non_filer',
            )
        gain = 3_000_000
        self.assertEqual(r.cgt_amount, int(gain * 0.30))


class CGTYear3FilerTest(TestCase):
    """g — CGT year 3+ filer = 10%."""

    def test_g_cgt_year3_plus_filer(self):
        with patch('apps.config.services.SystemConfigService.get', return_value=None):
            r = PakistanFBRCalculator().calculate_capital_gains_tax(
                purchase_price=5_000_000, sale_price=8_000_000,
                holding_years=5, filer_status='filer',
            )
        gain = 3_000_000
        self.assertEqual(r.cgt_amount, int(gain * 0.10))


class CGTYear3NonFilerTest(TestCase):
    """h — CGT year 3+ non-filer = 20%."""

    def test_h_cgt_year3_plus_non_filer(self):
        with patch('apps.config.services.SystemConfigService.get', return_value=None):
            r = PakistanFBRCalculator().calculate_capital_gains_tax(
                purchase_price=5_000_000, sale_price=8_000_000,
                holding_years=3, filer_status='non_filer',
            )
        gain = 3_000_000
        self.assertEqual(r.cgt_amount, int(gain * 0.20))


class ZeroGainTest(TestCase):
    """i — zero gain → zero CGT."""

    def test_i_zero_gain_returns_zero_cgt(self):
        with patch('apps.config.services.SystemConfigService.get', return_value=None):
            r = PakistanFBRCalculator().calculate_capital_gains_tax(
                purchase_price=8_000_000, sale_price=8_000_000,
                holding_years=1, filer_status='filer',
            )
        self.assertEqual(r.cgt_amount, 0)
        self.assertEqual(r.wht_amount, 0)


class RegistryPKTest(TestCase):
    """j — registry returns PK calculator for PK."""

    def test_j_registry_returns_pk_calculator(self):
        calc = get_tax_calculator('PK')
        self.assertIsInstance(calc, PakistanFBRCalculator)
        self.assertTrue(calc.supported)

    def test_k_registry_returns_unsupported_for_ae(self):
        calc = get_tax_calculator('AE')
        self.assertIsInstance(calc, BaseTaxCalculator)
        self.assertFalse(calc.supported)


class SystemConfigOverrideTest(TestCase):
    """l — SystemConfig FBR key overrides hardcoded fallback."""

    def test_l_system_config_overrides_wht_rate(self):
        # Override tier1 filer rate to 5%
        def mock_get(key):
            return '0.05' if key == 'fbr_wht_filer_tier1_rate' else \
                   '0.02' if key == 'fbr_wht_filer_tier2_rate' else \
                   '0.03' if key == 'fbr_wht_filer_tier3_rate' else None

        with patch('apps.config.services.SystemConfigService.get', side_effect=mock_get):
            r = PakistanFBRCalculator().calculate_withholding_tax(2_000_000, 'filer')

        self.assertEqual(r.wht_amount, int(2_000_000 * 0.05))
