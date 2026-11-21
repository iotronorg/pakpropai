"""
T-1090c — query-parameter validation (GAP-031).

Covers:
  * apps.core.query_params helpers (int, limit/offset, choice)
  * endpoints that used bare int() casts: bad input → 400 with a field error
    in the ADR-017 envelope, never 500; negative limits rejected; large ones
    clamped as before
  * property list: enum params, price range, min_score bound, ref_no exact
    (KI-036), parameters documented in the OpenAPI schema
"""
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase
from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.core.query_params import choice_param, int_param, limit_offset
from apps.properties.models import Property
from tests.factories import make_client, make_org, make_property, make_user

PROPS = '/api/v1/properties/'


def _q(**kw):
    q = QueryDict(mutable=True)
    q.update(kw)
    return q


class HelperTest(SimpleTestCase):

    def test_int_param(self):
        self.assertIsNone(int_param(_q(), 'n'))
        self.assertEqual(int_param(_q(n='  '), 'n', 7), 7)
        self.assertEqual(int_param(_q(n='5,000,000'), 'n'), 5_000_000)
        self.assertEqual(int_param(_q(n='999'), 'n', max_value=10), 10)          # clamped
        with self.assertRaises(ValidationError):
            int_param(_q(n='999'), 'n', max_value=10, clamp=False)
        for bad in ('abc', '1.5', '1e3', '--1'):
            with self.subTest(bad=bad), self.assertRaises(ValidationError) as ctx:
                int_param(_q(n=bad), 'n')
            self.assertEqual(ctx.exception.detail, {'n': 'Must be a whole number.'})

    def test_lower_bounds(self):
        with self.assertRaises(ValidationError) as ctx:
            int_param(_q(n='-1'), 'n')
        self.assertEqual(ctx.exception.detail, {'n': 'Must not be negative.'})
        with self.assertRaises(ValidationError) as ctx:
            int_param(_q(n='0'), 'n', min_value=1)
        self.assertEqual(ctx.exception.detail, {'n': 'Must be at least 1.'})

    def test_limit_offset(self):
        self.assertEqual(limit_offset(_q()), (50, 0))
        self.assertEqual(limit_offset(_q(limit='9999', offset='3'), maximum=200), (200, 3))
        for bad in ({'limit': '0'}, {'limit': '-5'}, {'offset': '-1'}, {'limit': 'x'}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                limit_offset(_q(**bad))

    def test_choice_param(self):
        self.assertEqual(choice_param(_q(t='plot'), 't', ['plot', 'residential']), 'plot')
        self.assertIsNone(choice_param(_q(), 't', ['plot']))
        with self.assertRaises(ValidationError) as ctx:
            choice_param(_q(t='castle'), 't', ['plot', 'residential'])
        self.assertEqual(ctx.exception.detail, {'t': 'Must be one of: plot, residential.'})


class EndpointCastsTest(TestCase):
    """Each endpoint below returned 500 (or silently ignored input) before."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user(role='admin', is_staff=True)
        cls.user = make_client()

    def _get(self, user, url, **params):
        api = APIClient()
        api.raise_request_exception = False
        api.force_authenticate(user)
        return api.get(url, params)

    def _assert_field_400(self, resp, field):
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json()['error_info']['code'], 'validation_error')
        self.assertIn(field, resp.json()['error_info']['fields'])

    def test_audit_log(self):
        url = '/api/v1/audit-log/'
        self._assert_field_400(self._get(self.admin, url, limit='abc'), 'limit')
        self._assert_field_400(self._get(self.admin, url, limit='-5'), 'limit')   # negative slice → 500 before
        self._assert_field_400(self._get(self.admin, url, offset='-1'), 'offset')
        self.assertEqual(self._get(self.admin, url, limit='9999').json()['limit'], 200)

    def test_security_chain_verify_is_bounded(self):
        url = '/api/v1/security/chain-verify/'
        self._assert_field_400(self._get(self.admin, url, limit='lots'), 'limit')
        self.assertEqual(self._get(self.admin, url, limit='10000000').json()['records_checked'], 10_000)

    def test_notifications(self):
        url = '/api/v1/notifications/'
        self._assert_field_400(self._get(self.user, url, limit='abc'), 'limit')
        self._assert_field_400(self._get(self.user, url, offset='x'), 'offset')
        self.assertEqual(self._get(self.user, url, limit='2').status_code, 200)

    def test_verification_fraud_alerts(self):
        self._assert_field_400(self._get(self.admin, '/api/v1/verification/fraud/alerts/', limit='0'), 'limit')


class PropertyListParamsTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        org = make_org(name='P', operational_mode='production')
        cls.plot = make_property(org=org, title='plot', property_type='plot', price=2_000_000)
        cls.house = make_property(org=org, title='house', property_type='residential', price=9_000_000)
        cls.user = make_client()

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def _titles(self, **params):
        resp = self.api.get(PROPS, params)
        self.assertEqual(resp.status_code, 200, resp.content)
        return {r['title'] for r in resp.json()['results']}

    def test_valid_enum_filters(self):
        self.assertEqual(self._titles(type='plot'), {'plot'})
        self.assertEqual(self._titles(legal_status='unverified'), {'plot', 'house'})

    def test_unknown_enum_is_400(self):
        for params in ({'type': 'castle'}, {'legal_status': 'approved'}):
            with self.subTest(params=params):
                resp = self.api.get(PROPS, params)
                self.assertEqual(resp.status_code, 400)
                self.assertIn('Must be one of', str(resp.json()))

    def test_price_range(self):
        self.assertEqual(self._titles(min_price='1,000,000', max_price='5000000'), {'plot'})
        resp = self.api.get(PROPS, {'min_price': 9, 'max_price': 1})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('min_price', resp.json()['error_info']['fields'])

    def test_min_score_bounded(self):
        self.assertEqual(self.api.get(PROPS, {'min_score': 101}).status_code, 400)

    def test_ref_no_exact_case_insensitive(self):
        ref = Property.objects.get(pk=self.plot.pk).ref_no
        self.assertEqual(self._titles(ref_no=f' {ref.lower()} '), {'plot'})
        self.assertEqual(self._titles(ref_no=ref[:-1]), set())   # no partial match

    def test_ref_no_filter_uses_unique_index(self):
        # KI-036: iexact (UPPER(ref_no)) could not use the index.
        sql = str(Property.objects.filter(ref_no='RT-X').query)
        self.assertNotIn('UPPER', sql)


class SchemaParamsTest(SimpleTestCase):

    def test_property_list_params_documented_with_enums(self):
        GENERATOR_STATS.reset()
        with GENERATOR_STATS.silence():
            schema = SchemaGenerator().get_schema(request=None, public=True)
        GENERATOR_STATS.reset()
        params = {p['name']: p for p in schema['paths'][PROPS]['get']['parameters']}
        for name in ('search', 'ref_no', 'city', 'type', 'legal_status', 'min_price', 'max_price', 'min_score'):
            self.assertIn(name, params)
        self.assertEqual(set(params['type']['schema']['enum']), set(Property.PropertyType.values))
        self.assertEqual(params['min_price']['schema']['type'], 'integer')
