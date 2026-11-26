"""T-4010 — investment calculator and labelled commentary (FR-INV-001…004, ADR-065)."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.ai import investment_commentary as IC
from apps.config.models import SystemConfig
from apps.properties import investment as I
from apps.properties.models import Property
from tests.factories import make_client, make_developer, make_org, make_property as _make_property
from tests.test_runtime import LOCMEM

URL = '/api/v1/investment/'


def make_property(sandbox=False, **kw):
    p = _make_property(**kw)                        # save() derives is_sandbox (platform demo = sandbox)
    Property.objects.filter(pk=p.pk).update(is_sandbox=sandbox)
    p.is_sandbox = sandbox
    return p


def fig(result, name):
    return next(f['value'] for f in result['figures'] if f['name'] == name)


def source(result, name):
    return next(a['source'] for a in result['assumptions'] if a['name'] == name)


@override_settings(CACHES=LOCMEM)
class CalculatorTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_cash_purchase_formulas_and_sources(self):
        r = I.calculate({'price': 10_000_000, 'monthly_rent': 50_000, 'country': 'US', 'currency': 'USD',
                         'vacancy_pct': 0, 'maintenance_pct': 0, 'annual_property_tax': 0, 'purchase_costs': 0})
        self.assertEqual(fig(r, 'gross_annual_rent'), 600_000)
        self.assertEqual(fig(r, 'gross_yield_pct'), 6.0)
        self.assertEqual(fig(r, 'net_yield_pct'), 6.0)
        self.assertEqual(fig(r, 'cash_on_cash_pct'), 6.0)
        self.assertEqual(fig(r, 'loan_amount'), 0)
        self.assertEqual(source(r, 'monthly_rent'), 'user')
        self.assertEqual(source(r, 'management_pct'), 'platform_default')
        self.assertTrue(all(f['formula'] for f in r['figures']))
        self.assertIn('not financial advice', r['disclaimer'])
        self.assertEqual(r['scenarios'], [])                      # no growth assumed by the platform

    def test_loan_market_fees_and_scenarios(self):
        r = I.calculate({'price': 1_000_000, 'monthly_rent': 6_000, 'country': 'AE', 'currency': 'AED',
                         'down_payment_pct': 25, 'interest_rate_pct': 6, 'tenure_years': 25,
                         'appreciation_pct': [0, 3]})
        self.assertEqual(fig(r, 'loan_amount'), 750_000)
        self.assertEqual(fig(r, 'monthly_loan_payment'), 4832)    # standard annuity
        self.assertEqual(source(r, 'purchase_costs'), 'market_calculator')
        self.assertEqual(fig(r, 'cash_invested'), 250_000 + 44_000)   # DLD 4% + AED 4,000
        flat, up = r['scenarios']
        self.assertEqual(flat['value_at_end'], 1_000_000)
        self.assertGreater(up['value_at_end'], 1_000_000)
        self.assertLess(flat['loan_balance_at_end'], 750_000)
        self.assertEqual(I.calculate({'price': 1_000_000, 'monthly_rent': 6_000, 'country': 'ZZ',
                                      'currency': 'XXX'})['assumptions'][-1]['source'], 'platform_default')

    def test_validation(self):
        ok = {'price': 1_000_000, 'monthly_rent': 5_000, 'country': 'US', 'currency': 'USD'}
        for bad, field in (({'price': -1}, 'price'), ({'price': 'abc'}, 'price'), ({'vacancy_pct': 101}, 'vacancy_pct'),
                           ({'price': 'NaN'}, 'price'), ({'down_payment_pct': 20}, 'interest_rate_pct'),
                           ({'appreciation_pct': [1, 2, 3, 4]}, 'appreciation_pct'), ({'country': ''}, 'country')):
            with self.assertRaises(I.InvestmentError) as e:
                I.calculate({**ok, **bad})
            self.assertEqual(e.exception.field, field, bad)
        with self.assertRaises(I.InvestmentError) as e:
            I.calculate({'price': 1, 'country': 'US', 'currency': 'USD'})
        self.assertEqual(e.exception.field, 'monthly_rent')

    def test_rent_from_comparables_with_sample_size(self):
        sale = make_property(title='For sale', country='PK', currency='PKR', city='Lahore', price=20_000_000,
                             bedrooms=3, purpose='sale', price_type='total')
        for i, rent in enumerate((80_000, 90_000, 100_000, 110_000)):
            make_property(title=f'r{i}', country='PK', currency='PKR', city='Lahore', price=rent, bedrooms=3,
                          purpose='rent_long', price_type='per_month')
        with self.assertRaises(I.InvestmentError):                # 4 < MIN_COMPARABLES
            I.calculate({}, listing=sale)
        make_property(title='yearly', country='PK', currency='PKR', city='Lahore', price=1_200_000, bedrooms=3,
                      purpose='rent_long', price_type='per_year')
        make_property(title='hidden', country='PK', currency='PKR', city='Lahore', price=999_999, bedrooms=3,
                      purpose='rent_long', price_type='per_month', is_active=False)
        make_property(title='other city', country='PK', currency='PKR', city='Karachi', price=1, bedrooms=3,
                      purpose='rent_long', price_type='per_month')
        r = I.calculate({}, listing=sale)
        rent = next(a for a in r['assumptions'] if a['name'] == 'monthly_rent')
        self.assertEqual((rent['source'], rent['value'], rent['detail']['n']), ('platform_median', 100_000, 5))
        self.assertEqual(source(r, 'purchase_costs'), 'market_calculator')
        self.assertIn('236K', ' '.join(r['notes']))
        rent_listing = make_property(title='a rent', country='PK', currency='PKR', price=1, purpose='rent_long',
                                     price_type='per_month')
        with self.assertRaises(I.InvestmentError) as e:
            I.calculate({}, listing=rent_listing)
        self.assertEqual(e.exception.field, 'listing')


class CommentaryCheckTest(TestCase):
    def result(self):
        return I.calculate({'price': 1_000_000, 'monthly_rent': 6_000, 'country': 'US', 'currency': 'USD',
                            'appreciation_pct': [3]})

    def test_numbers_and_advice_are_checked(self):
        r = self.result()
        allowed = IC._allowed(r)
        gross = fig(r, 'gross_yield_pct')
        self.assertEqual(IC.check(f'The gross yield is {gross}% on a price of 1,000,000.', allowed), [])
        self.assertEqual(IC.check('The gross yield is 9.9%.', allowed), ['number_not_in_result'])
        self.assertEqual(IC.check('Prices will rise, so you should buy.', allowed), ['advice_or_promise'])
        self.assertEqual(IC.check('Over 5 years with 2 scenarios.', allowed), [])

    def test_commentary_keeps_only_grounded_paragraphs(self):
        r = self.result()
        reply = {'paragraphs': [f"Gross yield is {fig(r, 'gross_yield_pct')}%.", 'A guaranteed 15% return.',
                                'Net yield would be 12.34%.', '']}
        with patch('apps.ai.llm.complete_json', return_value=reply) as m:
            out = IC.commentary(r)
        self.assertEqual(out['paragraphs'], [reply['paragraphs'][0]])
        self.assertEqual([x['problems'] for x in out['left_out']],
                         [['advice_or_promise', 'number_not_in_result'], ['number_not_in_result']])
        self.assertEqual(out['label'], IC.LABEL)
        self.assertEqual(m.call_args.kwargs['interaction_type'], 'investment_commentary')
        self.assertIn('gross_yield_pct', m.call_args[0][1]['result'])


@override_settings(CACHES=LOCMEM)
class ApiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.body = {'price': 1_000_000, 'monthly_rent': 6_000, 'country': 'US', 'currency': 'USD'}

    def test_calculate_is_public_and_validates(self):
        api = APIClient()
        r = api.post(URL + 'calculate/', self.body, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(fig(r.data, 'gross_annual_rent'), 72_000)
        self.assertEqual(api.post(URL + 'calculate/', {**self.body, 'price': 'x'}, format='json').status_code, 400)
        self.assertEqual(api.post(URL + 'calculate/', ['x'], format='json').status_code, 400)
        self.assertEqual(api.post(URL + 'calculate/', {'listing': 'nope'}, format='json').status_code, 404)

    def test_listing_lookup_public_or_managed(self):
        _, org = make_developer(org=make_org(name='Prime'))
        public = make_property(title='Public', country='US', currency='USD', price=500_000)
        r = APIClient().post(URL + 'calculate/', {'listing': str(public.pk), 'monthly_rent': 3000}, format='json')
        self.assertEqual((r.status_code, r.data['price'], r.data['listing_id']), (200, 500_000, str(public.pk)))
        owner, org = make_developer(org=make_org(name='Draft org'))
        draft = make_property(org=org, owner=owner, title='Draft', country='US', currency='USD', price=400_000, sandbox=True)
        self.assertEqual(APIClient().post(URL + 'calculate/', {'listing': str(draft.pk), 'monthly_rent': 1},
                                          format='json').status_code, 404)
        api = APIClient()
        api.force_authenticate(owner)
        self.assertEqual(api.post(URL + 'calculate/', {'listing': str(draft.pk), 'monthly_rent': 1},
                                  format='json').status_code, 200)
        stranger = APIClient()
        stranger.force_authenticate(make_client())
        self.assertEqual(stranger.post(URL + 'calculate/', {'listing': str(draft.pk), 'monthly_rent': 1},
                                       format='json').status_code, 404)

    def test_commentary_flag_auth_and_unavailable(self):
        from apps.ai.llm import LLMUnavailable
        self.assertIn(APIClient().post(URL + 'commentary/', self.body, format='json').status_code, (401, 403))
        api = APIClient()
        api.force_authenticate(make_client())
        self.assertEqual(api.post(URL + 'commentary/', self.body, format='json').status_code, 403)   # off by default
        SystemConfig.objects.update_or_create(key='feature_investment_commentary', defaults={'value': 'true'})
        cache.clear()
        with patch('apps.ai.llm.complete_json', return_value={'paragraphs': ['Gross yield is 7.2%.']}):
            r = api.post(URL + 'commentary/', self.body, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['commentary']['paragraphs'], ['Gross yield is 7.2%.'])
        self.assertEqual(fig(r.data, 'gross_yield_pct'), 7.2)
        with patch('apps.ai.llm.complete_json', side_effect=LLMUnavailable('x')):
            r = api.post(URL + 'commentary/', self.body, format='json')
        self.assertEqual((r.status_code, r.data['code'], fig(r.data, 'gross_annual_rent')), (503, 'ai.unavailable', 72_000))
