"""
Tests for global audit engine, PDF area display, OrgType choices,
and GB financing module.
"""
from django.test import TestCase


# ─── Audit Service ────────────────────────────────────────────────────────────

class AuditServiceGlobalTest(TestCase):
    """AuditEngine.run() works with area_sqm input (non-PK path)."""

    def test_run_with_area_sqm_produces_report(self):
        from apps.audit.services import AuditEngine
        report = AuditEngine.run(
            city='dubai',
            location='downtown',
            property_type='apartment',
            estimated_value=2_500_000,
            value_currency='AED',
            area_sqm=120.0,
        )
        self.assertIn('overview', report)
        self.assertIn('financial_analysis', report)
        self.assertIsNone(report['overview']['estimated_value_pkr'])
        self.assertIn('value_currency', report['overview'])
        self.assertEqual(report['overview']['value_currency'], 'AED')

    def test_run_with_area_marla_still_works(self):
        from apps.audit.services import AuditEngine
        report = AuditEngine.run(
            city='lahore',
            location='dha',
            property_type='house',
            estimated_value=15_000_000,
            value_currency='PKR',
            area_marla=5.0,
        )
        self.assertIn('financial_analysis', report)
        self.assertEqual(report['overview']['value_currency'], 'PKR')
        self.assertEqual(report['overview']['estimated_value_pkr'], 15_000_000)

    def test_legacy_estimated_value_pkr_param_still_works(self):
        from apps.audit.services import AuditEngine
        report = AuditEngine.run(
            city='karachi',
            location='clifton',
            property_type='apartment',
            estimated_value_pkr=8_000_000,
            area_marla=3.0,
        )
        self.assertEqual(report['overview']['estimated_value'], 8_000_000)

    def test_area_sqm_derived_from_marla(self):
        from apps.audit.services import AuditEngine
        report = AuditEngine.run(
            city='lahore',
            location='default',
            property_type='house',
            estimated_value=5_000_000,
            value_currency='PKR',
            area_marla=5.0,
        )
        # 5 marla * 25.2929 ≈ 126.46 sqm
        self.assertAlmostEqual(report['overview']['area_sqm'], 126.46, delta=0.5)


# ─── PDF Area Display ─────────────────────────────────────────────────────────

class AuditPDFAreaDisplayTest(TestCase):

    def test_pk_measurement_shows_marla(self):
        from apps.audit.pdf import _format_area_display
        result = _format_area_display(area_sqm=126.46, measurement_system='pk_traditional')
        self.assertIn('Marla', result)

    def test_imperial_shows_sqft(self):
        from apps.audit.pdf import _format_area_display
        result = _format_area_display(area_sqm=120.0, measurement_system='imperial')
        self.assertIn('sqft', result)

    def test_metric_shows_sqm(self):
        from apps.audit.pdf import _format_area_display
        result = _format_area_display(area_sqm=120.0, measurement_system='metric')
        self.assertIn('m²', result)
        self.assertIn('120', result)

    def test_none_area_returns_na(self):
        from apps.audit.pdf import _format_area_display
        result = _format_area_display(area_sqm=None, measurement_system='metric')
        self.assertEqual(result, 'N/A')


# ─── OrgType ─────────────────────────────────────────────────────────────────

class OrgTypeCommunityDevelopmentTest(TestCase):

    def test_community_development_choice_exists(self):
        from apps.organizations.models import Organization
        choices = [c[0] for c in Organization.OrgType.choices]
        self.assertIn('community_development', choices)

    def test_housing_society_is_deprecated_alias(self):
        from apps.organizations.models import Organization
        choices = [c[0] for c in Organization.OrgType.choices]
        self.assertIn('housing_society', choices)

    def test_community_development_label_correct(self):
        from apps.organizations.models import Organization
        labels = {c[0]: c[1] for c in Organization.OrgType.choices}
        self.assertEqual(labels['community_development'], 'Community Development')
        self.assertIn('deprecated', labels['housing_society'].lower())


# ─── GB Financing ─────────────────────────────────────────────────────────────

class GBFinancingModuleTest(TestCase):

    def test_gb_financing_importable(self):
        from apps.markets.gb.financing import GBCalculator
        self.assertTrue(callable(GBCalculator))

    def test_registry_returns_gb_calculator(self):
        from apps.markets.registry import get_financing_calculator
        calc = get_financing_calculator('GB')
        self.assertIsNotNone(calc)

    def test_registry_returns_pk_calculator(self):
        from apps.markets.registry import get_financing_calculator
        calc = get_financing_calculator('PK')
        self.assertIsNotNone(calc)

    def test_registry_returns_ae_calculator(self):
        from apps.markets.registry import get_financing_calculator
        calc = get_financing_calculator('AE')
        self.assertIsNotNone(calc)

    def test_registry_returns_none_for_unknown_market(self):
        from apps.markets.registry import get_financing_calculator
        calc = get_financing_calculator('XX')
        self.assertIsNone(calc)

    def test_gb_calculator_check_loan(self):
        from apps.markets.gb.financing import GBCalculator
        calc = GBCalculator()
        result = calc.check_loan(monthly_income=5000, loan_amount=250_000)
        self.assertEqual(result.country, 'GB')
        self.assertTrue(result.supported)
