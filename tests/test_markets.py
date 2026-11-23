"""
T-1050a / ADR-019 — Market entity and market-driven defaults.

Covers:
  * seed rows exist and agree with the code registry
  * Market.clean validation; admin cannot delete; PROTECT on referenced rows
  * get_market: DB row, cache invalidation on save, registry fallback, unknown
  * model defaults follow settings.DEFAULT_MARKET (no literal 'PKR'/'PK'/'marla')
  * Property/Organization derive market from country on create
  * backfill migrations and the market_backfill_report command
  * M1 exit criterion: static check that no literal market default exists
    outside the market packs, plus a ratchet on remaining PK literals
"""
import importlib
import io
import pathlib
import re

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.escrow.models import EscrowDeal
from apps.leads.models import Lead
from apps.markets.models import Market
from apps.markets.registry import MARKET_REGISTRY
from apps.markets.services import (
    default_area_unit, default_country, default_currency, default_market_code,
    get_market, market_code_for_country, registry_info,
)
from apps.organizations.models import Organization
from apps.payments.models import Payment
from apps.properties.models import Property
from tests.factories import make_client, make_org, make_property

ROOT = pathlib.Path(__file__).resolve().parents[1]


class SeedTest(TestCase):

    def test_all_registry_markets_seeded_and_consistent(self):
        self.assertEqual(set(Market.objects.values_list('code', flat=True)), set(MARKET_REGISTRY))
        for code in MARKET_REGISTRY:
            with self.subTest(code=code):
                row, reg = Market.objects.get(code=code), registry_info(code)
                self.assertEqual(row.default_currency, reg.default_currency)
                self.assertEqual(row.default_area_unit, reg.default_area_unit)
                self.assertEqual(list(row.area_units), list(reg.area_units))
                self.assertEqual(row.default_language, reg.default_language)
                self.assertEqual(list(row.languages), list(reg.languages))
                self.assertEqual(row.timezone, reg.timezone)
                self.assertEqual(row.name, reg.name)
                row.full_clean()

    def test_only_pk_is_launched(self):
        self.assertEqual(list(Market.objects.filter(status='active').values_list('code', flat=True)), ['PK'])

    def test_seed_is_idempotent_and_keeps_operator_edits(self):
        Market.objects.filter(code='AE').update(status='active')
        mod = importlib.import_module('apps.markets.migrations.0002_seed_markets')
        mod.seed(django_apps, None)
        self.assertEqual(Market.objects.get(code='AE').status, 'active')
        self.assertEqual(Market.objects.count(), len(mod.SEED))


class MarketValidationTest(TestCase):

    def _market(self, **kw):
        base = dict(code='DE', name='Germany', default_currency='EUR', currencies=['EUR'],
                    default_area_unit='sqm', area_units=['sqm'], default_language='de',
                    languages=['de', 'en'], timezone='Europe/Berlin')
        return Market(**{**base, **kw})

    def test_valid(self):
        self._market().full_clean()

    def test_invalid_fields(self):
        cases = {
            'code': {'code': 'de'},
            'default_currency': {'default_currency': 'USD'},
            'default_area_unit': {'default_area_unit': 'marla'},
            'default_language': {'default_language': 'fr'},
            'timezone': {'timezone': 'Mars/Olympus'},
        }
        for field, kw in cases.items():
            with self.subTest(field=field), self.assertRaises(ValidationError) as ctx:
                self._market(**kw).full_clean()
            self.assertIn(field, ctx.exception.message_dict)

    def test_referenced_market_cannot_be_deleted(self):
        make_property()
        with self.assertRaises(ProtectedError):
            Market.objects.get(code='PK').delete()

    def test_admin_cannot_delete(self):
        from django.contrib.admin.sites import site
        self.assertFalse(site._registry[Market].has_delete_permission(None))


class GetMarketTest(TestCase):

    def test_db_row_is_used_and_cache_invalidated_on_save(self):
        self.assertTrue(get_market('PK').from_db)
        self.assertEqual(get_market('pk').timezone, 'Asia/Karachi')
        row = Market.objects.get(code='PK')
        row.price_format = 'edited'
        row.save()
        self.assertEqual(get_market('PK').price_format, 'edited')

    def test_default_market(self):
        self.assertEqual(get_market().code, 'PK')

    def test_registry_fallback_when_row_missing(self):
        Market.objects.filter(code='CA').delete()   # unreferenced
        info = get_market('CA')
        self.assertFalse(info.from_db)
        self.assertEqual(info.default_currency, 'CAD')

    def test_unknown_market(self):
        self.assertIsNone(get_market('ZZ'))
        self.assertIsNone(market_code_for_country('ZZ'))
        self.assertIsNone(market_code_for_country(''))
        self.assertEqual(market_code_for_country(' ae '), 'AE')


class DefaultsFollowSettingTest(TestCase):

    def test_pk_by_default(self):
        self.assertEqual((default_country(), default_currency(), default_area_unit()), ('PK', 'PKR', 'marla'))
        p = Property()
        self.assertEqual((p.country, p.currency, p.area_unit), ('PK', 'PKR', 'marla'))

    @override_settings(DEFAULT_MARKET='AE')
    def test_other_default_market(self):
        p = Property()
        self.assertEqual((p.country, p.currency, p.area_unit), ('AE', 'AED', 'sqft'))
        self.assertEqual(Organization().country, 'AE')
        self.assertEqual(Lead().budget_currency, 'AED')
        self.assertEqual(Payment().currency, 'AED')
        self.assertEqual(EscrowDeal().currency, 'AED')

    @override_settings(DEFAULT_MARKET='XX')
    def test_unknown_default_market_fails_loudly(self):
        with self.assertRaises(LookupError):
            default_market_code()


class DeriveMarketTest(TestCase):

    def test_property_market_from_country(self):
        self.assertEqual(make_property().market_id, 'PK')
        self.assertEqual(make_property(country='ae').market_id, 'AE')
        self.assertIsNone(make_property(country='ZZ').market_id)

    def test_explicit_market_is_kept(self):
        self.assertEqual(make_property(country='PK', market_id='GB').market_id, 'GB')

    def test_market_follows_country_change(self):
        # Owner decision 2026-11-23 (KI-050): a country change re-derives the market.
        prop = make_property()
        prop.country = 'AE'
        prop.save()
        prop.refresh_from_db()
        self.assertEqual(prop.market_id, 'AE')

    def test_organization_market_from_country(self):
        self.assertEqual(make_org(country='GB').market_id, 'GB')
        self.assertEqual(make_org().market_id, 'PK')


class BackfillTest(TestCase):

    def test_backfill_migrations_fill_null_markets(self):
        prop = make_property(country='AE')
        zz = make_property(country='ZZ')
        org = make_org(country='US')
        Property.objects.update(market=None)
        Organization.objects.update(market=None)
        for path in ('apps.properties.migrations.0024_backfill_market',
                     'apps.organizations.migrations.0026_backfill_market'):
            importlib.import_module(path).backfill(django_apps, None)
        prop.refresh_from_db(); zz.refresh_from_db(); org.refresh_from_db()
        self.assertEqual((prop.market_id, zz.market_id, org.market_id), ('AE', None, 'US'))

    def test_report_command(self):
        make_property(country='PK')
        make_property(country='ZZ')
        out = io.StringIO()
        call_command('market_backfill_report', stdout=out)
        text = out.getvalue()
        self.assertIn('unmatched=1 (50.00%)', text)
        self.assertIn('unmatched country ZZ: 1', text)
        with self.assertRaises(CommandError):
            call_command('market_backfill_report', '--max-unmatched-pct', '10', stdout=io.StringIO())

    def test_report_apply(self):
        prop = make_property(country='GB')
        Property.objects.update(market=None)
        call_command('market_backfill_report', '--apply', stdout=io.StringIO())
        prop.refresh_from_db()
        self.assertEqual(prop.market_id, 'GB')


# ── M1 exit criterion: no hardcoded market defaults outside the packs ────────

STRICT = re.compile(r"""default\s*=\s*(?:['"](?:PKR|PK|marla)['"]|(?:\w+\.)?AreaUnit\.MARLA\b)""")
RATCHET = {
    # Remaining literals in logic/formatting (T-1050b). Lower as they move to
    # the market pack; never raise.
    # T-1050b: 34 → 27 → 5. The 5 left are the multi-currency alias table in
    # ai/compliance_filters.py (PKR next to AED/USD/GBP…), not PK defaults.
    'pkr_literal': (re.compile(r"""['"]PKR\b"""), 5),
    'pk_fallback': (re.compile(r"""\bor\s+['"]PK['"]|,\s*['"]PK['"]\s*\)"""), 0),  # 20 → 0
    'karachi_tz': (re.compile(r"""['"]Asia/Karachi['"]"""), 0),
}


def _source_files():
    for path in sorted((ROOT / 'apps').rglob('*.py')):
        rel = path.relative_to(ROOT).as_posix()
        if '/migrations/' in rel or rel.startswith('apps/markets/') or '/tests' in rel or rel.endswith('tests.py'):
            continue
        yield rel, path.read_text(encoding='utf-8')


class HardcodedMarketDefaultsTest(SimpleTestCase):

    def test_no_literal_market_defaults(self):
        hits = [f'{rel}:{i}: {line.strip()}'
                for rel, text in _source_files()
                for i, line in enumerate(text.splitlines(), 1) if STRICT.search(line)]
        self.assertEqual(hits, [], 'Use apps.markets.services.default_* callables (ADR-019).')

    def test_remaining_pk_literals_do_not_grow(self):
        for name, (rx, budget) in RATCHET.items():
            with self.subTest(name=name):
                count = sum(len(rx.findall(text)) for _, text in _source_files())
                self.assertLessEqual(count, budget, f'{name}: {count} > {budget}. Use the market pack.')

    def test_scanner_catches_a_literal_default(self):
        self.assertTrue(STRICT.search("currency = models.CharField(max_length=3, default='PKR')"))
        self.assertTrue(STRICT.search("area_unit = models.CharField(default=AreaUnit.MARLA)"))
        self.assertFalse(STRICT.search("currency = models.CharField(default=default_currency)"))


class MarketNeutralHelpersTest(SimpleTestCase):
    """T-1050b-1: formatting and fallbacks follow the record / DEFAULT_MARKET."""

    def test_format_price(self):
        from apps.markets.services import format_price
        self.assertEqual(format_price(1500000, 'aed'), 'AED 1,500,000')
        self.assertEqual(format_price(1500000), 'PKR 1,500,000')
        self.assertEqual(format_price(None), 'Price not listed')
        self.assertEqual(format_price(0, 'GBP', missing='n/a'), 'n/a')

    def test_country_helpers(self):
        from types import SimpleNamespace
        from apps.markets.services import country_or_default, currency_for_country, org_country
        self.assertEqual(country_or_default(' ae '), 'AE')
        self.assertEqual(country_or_default(''), 'PK')
        self.assertEqual(org_country(None), 'PK')
        self.assertEqual(org_country(SimpleNamespace(country='gb')), 'GB')
        self.assertEqual(org_country(SimpleNamespace(country='')), 'PK')
        self.assertEqual(currency_for_country('AE'), 'AED')
        self.assertEqual(currency_for_country('ZZ'), 'PKR')  # unknown → default market

    @override_settings(DEFAULT_MARKET='AE')
    def test_fallbacks_follow_default_market(self):
        from apps.markets.services import country_or_default, format_price, org_country
        self.assertEqual(country_or_default(None), 'AE')
        self.assertEqual(org_country(None), 'AE')
        self.assertEqual(format_price(10), 'AED 10')

    def test_search_result_keeps_listing_currency(self):
        from apps.properties.scrapers.base import PropertyResult
        from apps.properties.search import PropertySearchService
        r = PropertyResult(source='pakprop', source_id='1', title='Villa', city='Dubai',
                           location='Marina', price=2500000, currency='AED')
        self.assertIn('AED 2,500,000', r.format_wa(1))
        d = PropertySearchService._result_to_dict(r)
        self.assertEqual((d['price_formatted'], d['currency']), ('AED 2,500,000', 'AED'))
        legacy = PropertyResult.from_dict({'source': 'zameen', 'source_id': '2', 'title': 'H',
                                           'city': 'Lahore', 'location': 'DHA', 'price': 10})
        self.assertIsNone(legacy.currency)  # cached dicts without currency still load
        self.assertIn('PKR 10', legacy.format_wa())


class SearchCurrencyDbTest(TestCase):
    def test_db_search_formats_in_listing_currency_without_extra_queries(self):
        from apps.properties.search import PropertySearchService
        from tests.factories import make_developer, make_property
        _, org = make_developer()
        make_property(org=org, city='Lahore', currency='AED', price=900000)
        make_property(org=org, city='Lahore', currency='PKR', price=700000)
        with self.assertNumQueries(2):           # listings + one amenity prefetch (T-1070d)
            results = PropertySearchService._from_db(city='Lahore', viewer_org=org)
            formatted = sorted(PropertySearchService._result_to_dict(r)['price_formatted'] for r in results)
        self.assertEqual(formatted, ['AED 900,000', 'PKR 700,000'])


class AuditCurrencyLabelsTest(TestCase):
    """T-1050b-2: audit money is labelled in the unit it is in (KI-045 residual: PK-only engine)."""

    def _run(self, currency):
        from apps.audit.services import AuditEngine
        return AuditEngine.run(city='Lahore', location='DHA Phase 5', property_type='house',
                               estimated_value=50_000_000, value_currency=currency, area_marla=10)

    def test_value_figures_use_value_currency_and_benchmarks_their_own(self):
        r = self._run('AED')
        self.assertEqual(r['market_analysis']['benchmark_currency'], 'PKR')
        buyer = r['role_insights']['buyer']['summary']
        self.assertIn('AED 50,000,000', buyer)
        self.assertNotIn('PKR', buyer)
        self.assertTrue(r['role_insights']['seller']['optimal_asking_price'].startswith('PKR '))
        self.assertIsNone(r['overview']['estimated_value_pkr'])

    def test_pkr_audit_unchanged(self):
        r = self._run('PKR')
        self.assertIn('PKR 50,000,000', r['role_insights']['buyer']['summary'])
        self.assertEqual(r['overview']['estimated_value_pkr'], 50_000_000)

    def test_pdf_money_follows_report_currency(self):
        from unittest import mock
        from apps.audit import pdf
        seen = {}

        def capture(audit, s):
            seen['value'] = pdf._money(5)
            seen['explicit'] = pdf._money(5, 'gbp')
            return []

        with mock.patch.object(pdf, '_page7_recommendations', side_effect=capture):
            pdf.generate_audit_pdf_bytes(self._run('AED'))
        self.assertEqual(seen, {'value': 'AED 5', 'explicit': 'GBP 5'})
        self.assertEqual(pdf._money(5), 'PKR 5')  # context reset after rendering

    def test_projection_today_row_shows_the_value(self):
        """KI-046: the 'Today (Year 0)' row read a key the engine never emits (always 0)."""
        from apps.audit import pdf
        elements = pdf._page5_investment(self._run('PKR'), pdf._styles())
        rows = [row for el in elements if hasattr(el, '_cellvalues') for row in el._cellvalues]
        today = next(row for row in rows if row and row[0] == 'Today (Year 0)')
        self.assertEqual(today[1], 'PKR 50,000,000')


class PkPackConstantsTest(SimpleTestCase):
    def test_constants_match_registry(self):
        from apps.markets import pk
        from apps.markets.registry import MARKET_REGISTRY
        cfg = MARKET_REGISTRY['PK']
        self.assertEqual((pk.COUNTRY, pk.CURRENCY, pk.AREA_UNIT), (cfg.country, cfg.currency, cfg.size_unit))
