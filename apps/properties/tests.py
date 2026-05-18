from unittest.mock import patch

from django.test import TestCase

from apps.properties.financial import FinancialCalculator, FinancialConfig, FinancialConfigLoader
from apps.properties.search import PAGE_SIZE, PropertySearchService, SearchParams


# ── SearchParams ──────────────────────────────────────────────────────────────

class SearchParamsFromDictTest(TestCase):

    def test_maps_ai_tool_kwargs(self):
        p = SearchParams.from_dict({
            'city': 'Lahore', 'property_type': 'residential',
            'area_marla': 5.0, 'max_price_pkr': 10_000_000,
            'location': 'DHA Phase 6', 'furnished': 'furnished',
            'construction_status': 'ready',
        })
        self.assertEqual(p.city, 'Lahore')
        self.assertEqual(p.max_price, 10_000_000)
        self.assertAlmostEqual(p.area_marla, 5.0)
        self.assertEqual(p.furnished_status, 'furnished')
        self.assertEqual(p.construction_status, 'ready')

    def test_accepts_max_price_alias(self):
        p = SearchParams.from_dict({'max_price': 5_000_000})
        self.assertEqual(p.max_price, 5_000_000)

    def test_zero_values_stay_zero(self):
        p = SearchParams.from_dict({'city': 'Karachi', 'max_price_pkr': 0})
        self.assertEqual(p.max_price, 0)
        self.assertAlmostEqual(p.area_marla, 0.0)

    def test_missing_keys_use_defaults(self):
        p = SearchParams.from_dict({})
        self.assertEqual(p.city, '')
        self.assertEqual(p.page, 0)
        self.assertEqual(p.page_size, PAGE_SIZE)

    def test_installments_bool_cast(self):
        p = SearchParams.from_dict({'installments': True})
        self.assertTrue(p.installments)


# ── PropertySearchService.execute ─────────────────────────────────────────────

class PropertySearchServiceExecuteTest(TestCase):

    @patch('apps.properties.search.PropertySearchService._from_scrapers', return_value=[])
    def test_returns_structured_payload_keys(self, _):
        result = PropertySearchService.execute(SearchParams(city='Lahore'))
        for key in ('query', 'pagination', 'results', 'live_search_pending', 'whatsapp_message'):
            self.assertIn(key, result)

    @patch('apps.properties.search.PropertySearchService._from_scrapers', return_value=[])
    def test_pagination_keys_present(self, _):
        result = PropertySearchService.execute(SearchParams())
        pag = result['pagination']
        for key in ('page', 'page_size', 'total', 'has_next'):
            self.assertIn(key, pag)

    @patch('apps.properties.search.PropertySearchService._from_scrapers', return_value=[])
    def test_empty_results_returns_no_results_message(self, _):
        result = PropertySearchService.execute(SearchParams(city='Nowhere'))
        self.assertEqual(result['pagination']['total'], 0)
        self.assertIn('follow-up', result['whatsapp_message'])

    def test_is_duplicate_different_cities_not_dupes(self):
        from apps.properties.scrapers.base import PropertyResult
        a = PropertyResult(source='x', source_id='1', title='A', city='Lahore',
                           location='DHA', area_marla=5, price_pkr=10_000_000,
                           property_type='residential', url='', ai_score=None,
                           furnished_status=None, construction_status=None)
        b = PropertyResult(source='y', source_id='2', title='B', city='Karachi',
                           location='DHA', area_marla=5, price_pkr=10_000_000,
                           property_type='residential', url='', ai_score=None,
                           furnished_status=None, construction_status=None)
        self.assertFalse(PropertySearchService._is_duplicate(a, b))

    def test_is_duplicate_same_property_across_sources(self):
        from apps.properties.scrapers.base import PropertyResult
        a = PropertyResult(source='pakprop', source_id='1', title='House',
                           city='Lahore', location='dha phase 6', area_marla=5,
                           price_pkr=10_000_000, property_type='residential',
                           url='', ai_score=80, furnished_status=None,
                           construction_status=None)
        b = PropertyResult(source='zameen', source_id='ext-99', title='House',
                           city='Lahore', location='dha phase 6', area_marla=5.1,
                           price_pkr=10_200_000, property_type='residential',
                           url='https://zameen.com/x', ai_score=None,
                           furnished_status=None, construction_status=None)
        self.assertTrue(PropertySearchService._is_duplicate(a, b))

    def test_dedup_keeps_pakprop_over_scraper(self):
        from apps.properties.scrapers.base import PropertyResult
        pak = PropertyResult(source='pakprop', source_id='1', title='H',
                             city='Lahore', location='gulberg', area_marla=5,
                             price_pkr=8_000_000, property_type='residential',
                             url='', ai_score=70, furnished_status=None,
                             construction_status=None)
        ext = PropertyResult(source='zameen', source_id='ext', title='H',
                             city='Lahore', location='gulberg', area_marla=5,
                             price_pkr=8_100_000, property_type='residential',
                             url='https://zameen.com/h', ai_score=None,
                             furnished_status=None, construction_status=None)
        kept = PropertySearchService._dedup([pak, ext])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].source, 'pakprop')


# ── FinancialConfig ───────────────────────────────────────────────────────────

class FinancialConfigTest(TestCase):

    def test_from_dict_applies_float_override(self):
        cfg = FinancialConfig.from_dict({'stamp_duty_rate': '0.05', 'currency': 'AED'})
        self.assertAlmostEqual(cfg.stamp_duty_rate, 0.05)
        self.assertEqual(cfg.currency, 'AED')

    def test_from_dict_applies_int_override(self):
        cfg = FinancialConfig.from_dict({'annual_tax_threshold': '50000000'})
        self.assertEqual(cfg.annual_tax_threshold, 50_000_000)

    def test_from_dict_ignores_unknown_keys(self):
        cfg = FinancialConfig.from_dict({'nonexistent_key': '99'})
        self.assertEqual(cfg.country, 'PK')   # untouched

    def test_from_dict_invalid_value_keeps_default(self):
        cfg = FinancialConfig.from_dict({'stamp_duty_rate': 'not_a_number'})
        self.assertAlmostEqual(cfg.stamp_duty_rate, 0.03)   # default preserved


# ── FinancialCalculator ───────────────────────────────────────────────────────

class FinancialCalculatorTest(TestCase):

    def setUp(self):
        self.calc = FinancialCalculator(FinancialConfig())

    # EMI

    def test_emi_known_ballpark(self):
        emi = self.calc.calculate_emi(5_000_000, 0.22, 240)
        self.assertGreater(emi, 80_000)
        self.assertLess(emi, 120_000)

    def test_emi_zero_rate_equals_equal_instalments(self):
        emi = self.calc.calculate_emi(1_200_000, 0.0, 12)
        self.assertEqual(emi, 100_000)

    def test_max_affordable_loan_below_income(self):
        loan = self.calc.max_affordable_loan(0, 0, 0.22, 240)
        self.assertEqual(loan, 0)

    # Annual property tax

    def test_tax_exempt_below_threshold(self):
        r = self.calc.annual_property_tax(20_000_000, is_filer=True)
        self.assertEqual(r['tax'], 0)
        self.assertTrue(r['exempt'])

    def test_tax_1pct_for_filer_above_threshold(self):
        r = self.calc.annual_property_tax(30_000_000, is_filer=True)
        self.assertEqual(r['tax'], 300_000)   # 1 % of 30 M

    def test_tax_nonfiler_double_filer(self):
        filer    = self.calc.annual_property_tax(30_000_000, is_filer=True)
        nonfiler = self.calc.annual_property_tax(30_000_000, is_filer=False)
        self.assertEqual(nonfiler['tax'], filer['tax'] * 2)

    # Buyer costs

    def test_buyer_costs_sum_correctly(self):
        v = 10_000_000
        c = self.calc.buyer_transaction_costs(v)
        expected = v + int(v * 0.03) + int(v * 0.01) + int(v * 0.01) + 50_000
        self.assertEqual(c['total_true_cost'], expected)

    def test_buyer_costs_currency_propagated(self):
        calc_ae = FinancialCalculator(FinancialConfig(currency='AED'))
        c = calc_ae.buyer_transaction_costs(500_000)
        self.assertEqual(c['currency'], 'AED')

    # Seller net

    def test_seller_net_less_than_asking(self):
        r = self.calc.seller_net_proceeds(10_000_000, is_filer=True)
        self.assertLess(r['net_proceeds'], 10_000_000)

    def test_seller_nonfiler_pays_more_wht(self):
        filer    = self.calc.seller_net_proceeds(10_000_000, is_filer=True)
        nonfiler = self.calc.seller_net_proceeds(10_000_000, is_filer=False)
        self.assertGreater(filer['net_proceeds'], nonfiler['net_proceeds'])

    # CGT

    def test_cgt_zero_after_4_years(self):
        r = self.calc.capital_gains_tax(1_000_000, holding_years=4, is_filer=True)
        self.assertEqual(r['tax'], 0)

    def test_cgt_15pct_year1(self):
        r = self.calc.capital_gains_tax(1_000_000, holding_years=1, is_filer=True)
        self.assertEqual(r['tax'], 150_000)

    def test_cgt_nonfiler_higher(self):
        filer    = self.calc.capital_gains_tax(1_000_000, holding_years=1, is_filer=True)
        nonfiler = self.calc.capital_gains_tax(1_000_000, holding_years=1, is_filer=False)
        self.assertGreater(nonfiler['tax'], filer['tax'])

    # Loan eligibility

    def test_loan_ineligible_income_below_minimum(self):
        r = self.calc.loan_eligibility(monthly_income=20_000, loan_amount=5_000_000)
        self.assertFalse(r['eligible'])

    def test_loan_result_has_required_keys(self):
        r = self.calc.loan_eligibility(100_000, 5_000_000)
        for key in ('eligible', 'monthly_emi', 'down_payment_required', 'currency'):
            self.assertIn(key, r)

    def test_loan_down_payment_is_30pct(self):
        r = self.calc.loan_eligibility(200_000, 10_000_000)
        self.assertEqual(r['down_payment_required'], 3_000_000)

    # Full report

    def test_full_report_includes_required_sections(self):
        r = self.calc.full_report(
            property_value=30_000_000, monthly_income=200_000,
            loan_amount=21_000_000, is_filer=True,
            holding_years=2, estimated_gain=5_000_000,
        )
        for key in ('annual_property_tax', 'buyer_costs', 'seller_net',
                    'loan_eligibility', 'capital_gains_tax'):
            self.assertIn(key, r)

    def test_full_report_omits_loan_when_not_requested(self):
        r = self.calc.full_report(property_value=10_000_000)
        self.assertNotIn('loan_eligibility', r)

    def test_custom_config_rates_used(self):
        cfg  = FinancialConfig(stamp_duty_rate=0.05, currency='AED')
        calc = FinancialCalculator(cfg)
        c    = calc.buyer_transaction_costs(1_000_000)
        self.assertEqual(c['stamp_duty'], 50_000)
        self.assertEqual(c['currency'], 'AED')
