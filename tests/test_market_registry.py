"""
Market Configuration Engine — unit + regression tests.

These tests are pure Python: no database, no network, no LLM calls.
Run: python manage.py test tests.test_market_registry --settings=config.settings.test
"""
from django.test import SimpleTestCase

from apps.markets.registry import (
    MARKET_REGISTRY, MarketConfig, get_market_config, register_market,
)
from apps.markets.phone import PhoneCountryResolver
from apps.markets.engine import FinancialEngine


# ── Registry tests ─────────────────────────────────────────────────────────────

class MarketRegistryTest(SimpleTestCase):

    def test_pk_config_is_correct(self):
        cfg = get_market_config('PK')
        self.assertEqual(cfg.currency, 'PKR')
        self.assertEqual(cfg.size_unit, 'marla')
        self.assertAlmostEqual(cfg.sqft_per_unit, 272.25)

    def test_ae_config_is_correct(self):
        cfg = get_market_config('AE')
        self.assertEqual(cfg.currency, 'AED')
        self.assertEqual(cfg.size_unit, 'sqft')

    def test_gb_config_is_correct(self):
        cfg = get_market_config('GB')
        self.assertEqual(cfg.currency, 'GBP')

    def test_us_config_is_correct(self):
        cfg = get_market_config('US')
        self.assertEqual(cfg.currency, 'USD')

    def test_unknown_country_falls_back_to_pk(self):
        cfg = get_market_config('XX')
        self.assertEqual(cfg.country, 'PK')

    def test_case_insensitive_lookup(self):
        self.assertEqual(get_market_config('ae').currency, 'AED')
        self.assertEqual(get_market_config('Gb').currency, 'GBP')

    def test_all_required_markets_present(self):
        for code in ('PK', 'AE', 'GB', 'US', 'CA'):
            with self.subTest(country=code):
                cfg = get_market_config(code)
                self.assertEqual(cfg.country, code)
                self.assertTrue(cfg.currency)
                self.assertTrue(cfg.size_unit)


# ── Phone resolver tests ───────────────────────────────────────────────────────

class PhoneCountryResolverTest(SimpleTestCase):

    def test_pakistan_number(self):
        self.assertEqual(PhoneCountryResolver.resolve('+923001234567'), 'PK')

    def test_uae_number(self):
        self.assertEqual(PhoneCountryResolver.resolve('+971501234567'), 'AE')

    def test_uk_number(self):
        self.assertEqual(PhoneCountryResolver.resolve('+447700900123'), 'GB')

    def test_us_number(self):
        self.assertEqual(PhoneCountryResolver.resolve('+12125551234'), 'US')

    def test_empty_string_returns_pk_fallback(self):
        self.assertEqual(PhoneCountryResolver.resolve(''), 'PK')

    def test_number_without_leading_plus(self):
        self.assertEqual(PhoneCountryResolver.resolve('971501234567'), 'AE')

    def test_saudi_number(self):
        self.assertEqual(PhoneCountryResolver.resolve('+966551234567'), 'SA')

    def test_india_number(self):
        self.assertEqual(PhoneCountryResolver.resolve('+919876543210'), 'IN')

    def test_unknown_prefix_returns_pk_fallback(self):
        self.assertEqual(PhoneCountryResolver.resolve('+9991234567'), 'PK')


# ── Financial calculator — dynamic dispatch ────────────────────────────────────

class FinancialEngineDispatchTest(SimpleTestCase):

    def test_pk_tax_filer_above_threshold(self):
        result = FinancialEngine.calculate_tax(
            'PK', fmv=30_000_000, filer_status='filer'
        )
        self.assertTrue(result.supported)
        self.assertEqual(result.country, 'PK')
        self.assertEqual(result.annual_tax, 300_000)   # 1% × 30M

    def test_pk_tax_non_filer_doubles_rate(self):
        fmv = 30_000_000
        filer   = FinancialEngine.calculate_tax('PK', fmv=fmv, filer_status='filer')
        nonfiler = FinancialEngine.calculate_tax('PK', fmv=fmv, filer_status='non_filer')
        self.assertEqual(nonfiler.annual_tax, filer.annual_tax * 2)

    def test_pk_tax_below_threshold_is_zero(self):
        result = FinancialEngine.calculate_tax(
            'PK', fmv=20_000_000, filer_status='filer'
        )
        self.assertEqual(result.annual_tax, 0)
        self.assertTrue(result.exempt)

    def test_pk_self_occupied_single_property_exempt(self):
        result = FinancialEngine.calculate_tax(
            'PK', fmv=50_000_000, filer_status='filer',
            properties_count=1, is_self_occupied=True,
        )
        self.assertTrue(result.exempt)
        self.assertEqual(result.annual_tax, 0)

    def test_ae_tax_has_dld_fee(self):
        result = FinancialEngine.calculate_tax('AE', fmv=1_000_000, is_purchase=True)
        self.assertTrue(result.supported)
        self.assertEqual(result.country, 'AE')
        # DLD = 4% of 1M + AED 4,000 registration fee
        self.assertEqual(result.transfer_tax, 44_000)

    def test_ae_tax_no_annual_income_tax(self):
        result = FinancialEngine.calculate_tax('AE', fmv=1_000_000, is_purchase=True)
        self.assertEqual(result.annual_tax, 0)

    def test_ae_sale_has_no_transfer_tax_on_seller(self):
        result = FinancialEngine.calculate_tax('AE', fmv=1_000_000, is_purchase=False)
        self.assertEqual(result.transfer_tax, 0)

    def test_gb_first_time_buyer_under_threshold_zero_sdlt(self):
        result = FinancialEngine.calculate_tax(
            'GB', fmv=400_000, buyer_type='first_time'
        )
        self.assertEqual(result.transfer_tax, 0)

    def test_gb_standard_buyer_above_first_band(self):
        # Standard buyer: 0% on first £250k, 5% on next £250k = £12,500
        result = FinancialEngine.calculate_tax('GB', fmv=500_000, buyer_type='standard')
        self.assertEqual(result.transfer_tax, 12_500)

    def test_gb_additional_property_surcharge(self):
        standard = FinancialEngine.calculate_tax('GB', fmv=500_000, buyer_type='standard')
        additional = FinancialEngine.calculate_tax('GB', fmv=500_000, buyer_type='additional')
        # 3% surcharge on full price = £15,000 extra
        self.assertEqual(additional.transfer_tax, standard.transfer_tax + 15_000)

    def test_us_tax_estimate(self):
        result = FinancialEngine.calculate_tax('US', fmv=500_000, annual_rate_pct=1.2)
        self.assertTrue(result.supported)
        self.assertEqual(result.annual_tax, 6_000)   # 1.2% × 500k

    def test_unsupported_country_returns_supported_false(self):
        result = FinancialEngine.calculate_tax('ZZ', fmv=1_000_000)
        self.assertFalse(result.supported)

    def test_pk_loan_eligible_apna_ghar(self):
        # Apna Ghar at 7% — EMI ~PKR 23k/month is well under 50% of 100k
        result = FinancialEngine.check_loan(
            'PK', monthly_income=100_000, loan_amount=3_000_000,
            scheme='apna_ghar',
        )
        self.assertTrue(result.supported)
        self.assertTrue(result.eligible)

    def test_pk_loan_ineligible_low_income(self):
        result = FinancialEngine.check_loan(
            'PK', monthly_income=10_000, loan_amount=3_000_000
        )
        self.assertTrue(result.supported)
        self.assertFalse(result.eligible)

    def test_ae_loan_eligible(self):
        result = FinancialEngine.check_loan(
            'AE', monthly_income=30_000, loan_amount=500_000
        )
        self.assertTrue(result.supported)
        self.assertTrue(result.eligible)

    def test_gb_loan_eligible(self):
        result = FinancialEngine.check_loan(
            'GB', monthly_income=5_000, loan_amount=150_000
        )
        self.assertTrue(result.supported)
        self.assertTrue(result.eligible)

    def test_loan_unsupported_country(self):
        result = FinancialEngine.check_loan(
            'ZZ', monthly_income=100_000, loan_amount=1_000_000
        )
        self.assertFalse(result.supported)

    def test_parallel_dispatch_no_shared_state(self):
        """Two different markets dispatched in sequence must not bleed state."""
        pk = FinancialEngine.calculate_tax('PK', fmv=30_000_000, filer_status='filer')
        ae = FinancialEngine.calculate_tax('AE', fmv=1_000_000, is_purchase=True)
        self.assertEqual(pk.country, 'PK')
        self.assertEqual(ae.country, 'AE')
        self.assertNotEqual(pk.annual_tax, ae.annual_tax)

    def test_tools_financial_wrapper_pk_compat_keys(self):
        """The _tools_financial.py wrapper must include backward-compat PK alias keys."""
        from apps.ai._tools_financial import calculate_7e_tax
        result = calculate_7e_tax(
            fmv_pkr=30_000_000, filer_status='filer', country='PK'
        )
        self.assertIn('fmv_pkr', result)
        self.assertIn('tax_7e_annual_pkr', result)
        self.assertIn('withholding_tax_on_sale_pkr', result)
        self.assertEqual(result['tax_7e_annual_pkr'], 300_000)

    def test_tools_financial_wrapper_ae_dispatch(self):
        """The wrapper must correctly dispatch UAE calculations."""
        from apps.ai._tools_financial import calculate_7e_tax
        result = calculate_7e_tax(
            fmv_pkr=1_000_000, filer_status='filer', country='AE'
        )
        self.assertTrue(result['supported'])
        self.assertEqual(result['country'], 'AE')

    def test_tools_loan_wrapper_pk(self):
        from apps.ai._tools_financial import check_loan_eligibility
        result = check_loan_eligibility(
            monthly_income_pkr=100_000, loan_amount_pkr=3_000_000, country='PK'
        )
        self.assertIn('estimated_monthly_emi_pkr', result)
        self.assertIn('max_affordable_loan_pkr', result)


# ── Regression: adding a new market must NOT mutate existing outputs ───────────

class MarketRegistryRegressionTest(SimpleTestCase):
    """
    Registers a new Saudi Arabia market DURING the test, then verifies
    PK, AE, and GB outputs are byte-for-byte identical to pre-registration.
    """

    def setUp(self):
        self._pk_before = FinancialEngine.calculate_tax(
            'PK', fmv=30_000_000, filer_status='filer'
        )
        self._ae_before = FinancialEngine.calculate_tax(
            'AE', fmv=1_000_000, is_purchase=True
        )
        self._gb_before = FinancialEngine.calculate_tax(
            'GB', fmv=500_000, buyer_type='standard'
        )

        from apps.markets.calculators.base import MarketCalculator, TaxResult, LoanResult

        class SaudiCalculator(MarketCalculator):
            country = 'SA'

            def calculate_tax(self, fmv, **kwargs):
                return TaxResult(
                    country='SA', supported=True,
                    transfer_tax=int(fmv * 0.05),
                    advice='Saudi Arabia: 5% Real Estate Transaction Tax (RETT).',
                )

            def check_loan(self, monthly_income, loan_amount, tenure_years=20, **kwargs):
                return LoanResult(country='SA', supported=True)

        FinancialEngine.register(SaudiCalculator())

    def test_pk_output_unchanged(self):
        after = FinancialEngine.calculate_tax('PK', fmv=30_000_000, filer_status='filer')
        self.assertEqual(after.annual_tax,     self._pk_before.annual_tax)
        self.assertEqual(after.country,        self._pk_before.country)
        self.assertEqual(after.withholding_tax, self._pk_before.withholding_tax)
        self.assertEqual(after.stamp_duty,     self._pk_before.stamp_duty)

    def test_ae_output_unchanged(self):
        after = FinancialEngine.calculate_tax('AE', fmv=1_000_000, is_purchase=True)
        self.assertEqual(after.transfer_tax, self._ae_before.transfer_tax)
        self.assertEqual(after.country,      self._ae_before.country)

    def test_gb_output_unchanged(self):
        after = FinancialEngine.calculate_tax('GB', fmv=500_000, buyer_type='standard')
        self.assertEqual(after.transfer_tax, self._gb_before.transfer_tax)

    def test_new_sa_market_is_functional(self):
        result = FinancialEngine.calculate_tax('SA', fmv=1_000_000)
        self.assertTrue(result.supported)
        self.assertEqual(result.country, 'SA')
        self.assertEqual(result.transfer_tax, 50_000)   # 5% RETT
