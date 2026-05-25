"""
Unit tests for report task helpers.
Covers: A9-GLOBAL-2 — tax advisory country gate.
"""
from unittest.mock import MagicMock

from django.test import TestCase, override_settings

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _make_report(country='PK', prop_price=30_000_000):
    """Build a minimal mock Report with configurable org country."""
    org = MagicMock()
    org.country = country

    prop = MagicMock()
    prop.price = prop_price
    prop.organization = org

    user = MagicMock()
    user.owned_organization = org

    report = MagicMock()
    report.property = prop
    report.user = user
    report.content = {}
    return report


@override_settings(CACHES=_LOCMEM)
class TaxAdvisoryCountryGateTests(TestCase):
    """_content_tax_advisory returns unsupported for non-PK orgs (A9-GLOBAL-2)."""

    def _run(self, country, **meta):
        from apps.reports.tasks import _content_tax_advisory
        report = _make_report(country=country)
        if meta:
            report.content = {'input': meta}
        return _content_tax_advisory(report)

    def test_pk_org_returns_supported_result(self):
        result = self._run('PK')
        self.assertTrue(result['supported'])
        self.assertEqual(result['country'], 'PK')
        self.assertIn('section_7e', result)
        self.assertIn('capital_gains_tax', result)
        self.assertIn('withholding_tax', result)

    def test_ae_org_returns_unsupported(self):
        result = self._run('AE')
        self.assertFalse(result['supported'])
        self.assertEqual(result['country'], 'AE')
        self.assertIn('message', result)
        self.assertNotIn('section_7e', result)

    def test_gb_org_returns_unsupported(self):
        result = self._run('GB')
        self.assertFalse(result['supported'])

    def test_us_org_returns_unsupported(self):
        result = self._run('US')
        self.assertFalse(result['supported'])

    def test_unknown_country_returns_unsupported(self):
        result = self._run('')
        self.assertFalse(result['supported'])

    def test_pk_filer_section_7e_calculated(self):
        result = self._run('PK', property_value=50_000_000, ownership_type='filer')
        self.assertEqual(result['section_7e']['taxable_value'], 25_000_000)
        self.assertEqual(result['section_7e']['rate_pct'], 1.0)
        self.assertEqual(result['section_7e']['annual_tax'], 250_000)

    def test_pk_non_filer_higher_rate(self):
        result = self._run('PK', property_value=50_000_000, ownership_type='non_filer')
        self.assertEqual(result['section_7e']['rate_pct'], 2.0)

    def test_pk_property_under_exemption_zero_7e(self):
        result = self._run('PK', property_value=20_000_000)
        self.assertEqual(result['section_7e']['annual_tax'], 0)
        self.assertEqual(result['section_7e']['taxable_value'], 0)


@override_settings(CACHES=_LOCMEM)
class ResolveOrgCountryTests(TestCase):
    """_resolve_org_country falls back gracefully when relations are missing."""

    def test_returns_property_org_country(self):
        from apps.reports.tasks import _resolve_org_country
        report = _make_report(country='AE')
        self.assertEqual(_resolve_org_country(report), 'AE')

    def test_falls_back_to_user_org_when_no_property(self):
        from apps.reports.tasks import _resolve_org_country
        report = _make_report(country='GB')
        report.property = None
        self.assertEqual(_resolve_org_country(report), 'GB')

    def test_returns_empty_when_no_org(self):
        from apps.reports.tasks import _resolve_org_country
        report = MagicMock()
        report.property = None
        report.user.owned_organization = None
        # attribute access on None raises AttributeError — should return ''
        type(report.user).owned_organization = property(
            lambda self: (_ for _ in ()).throw(Exception('no org'))
        )
        self.assertEqual(_resolve_org_country(report), '')
