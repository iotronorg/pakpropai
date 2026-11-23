"""
T-1070e — Market.listing_schema validates Property.attributes: schema format
checks (Market.clean), value validation, seeded PK/AE/GB schemas, API create/
update (incl. market change and untouched legacy attributes), and model clean.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.markets.listing_schema import attribute_errors, schema_errors
from apps.markets.models import Market
from apps.properties.models import Property
from tests.factories import make_developer, make_property

URL = '/api/v1/properties/'

SCHEMA = {
    'fields': {
        'possession': {'type': 'enum', 'values': ['available', 'not_available']},
        'corner':     {'type': 'boolean'},
        'permit':     {'type': 'string', 'max_length': 8, 'pattern': '^[A-Z0-9-]+$'},
        'lease':      {'type': 'integer', 'min': 0, 'max': 999},
        'rent':       {'type': 'number', 'min': 0},
    },
    'additional': 'reject',
}


class SchemaFormatTest(TestCase):
    def test_valid_and_empty(self):
        self.assertEqual(schema_errors(SCHEMA), [])
        self.assertEqual(schema_errors({}), [])

    def test_invalid_definitions(self):
        cases = [
            [],
            {'fields': [], 'additional': 'reject'},
            {'fields': {}, 'additional': 'maybe'},
            {'fields': {}, 'strict': True},
            {'fields': {'Bad Key': {'type': 'boolean'}}},
            {'fields': {'x': {'type': 'date'}}},
            {'fields': {'x': {'type': 'enum', 'values': []}}},
            {'fields': {'x': {'type': 'enum', 'values': ['a', 'a']}}},
            {'fields': {'x': {'type': 'integer', 'min': 5, 'max': 1}}},
            {'fields': {'x': {'type': 'integer', 'min': '0'}}},
            {'fields': {'x': {'type': 'string', 'pattern': '('}}},
            {'fields': {'x': {'type': 'string', 'max_length': 0}}},
            {'fields': {'x': {'type': 'boolean', 'values': ['a']}}},
        ]
        for schema in cases:
            with self.subTest(schema=schema):
                self.assertTrue(schema_errors(schema))

    def test_market_clean_rejects_bad_schema(self):
        m = Market.objects.get(code='US')
        m.listing_schema = {'fields': {'x': {'type': 'date'}}}
        with self.assertRaises(ValidationError) as cm:
            m.full_clean()
        self.assertIn('listing_schema', cm.exception.message_dict)


class ValueValidationTest(TestCase):
    def test_values(self):
        ok = {'possession': 'available', 'corner': True, 'permit': 'AB-12',
              'lease': 99, 'rent': 12.5, 'lease_none': None}
        errs = attribute_errors(SCHEMA, ok)
        self.assertEqual(set(errs), {'lease_none'})          # unknown key, even when null
        self.assertEqual(attribute_errors(SCHEMA, {'lease': None}), {})
        bad = {'possession': 'soon', 'corner': 'yes', 'permit': 'ab', 'lease': 1.5,
               'rent': -1, 'unknown': 1}
        self.assertEqual(set(attribute_errors(SCHEMA, bad)), set(bad))
        self.assertIn('permit', attribute_errors(SCHEMA, {'permit': 'TOO-LONG-1'}))
        self.assertIn('lease', attribute_errors(SCHEMA, {'lease': True}))     # bool is not a number
        self.assertEqual(attribute_errors(SCHEMA, {'lease': 10.0}), {})

    def test_additional_allow_and_no_schema(self):
        self.assertEqual(attribute_errors(dict(SCHEMA, additional='allow'), {'x': 1}), {})
        self.assertEqual(attribute_errors({}, {'anything': 1}), {})


class SeedTest(TestCase):
    def test_seeded_markets(self):
        for code in ('PK', 'AE', 'GB'):
            schema = Market.objects.get(code=code).listing_schema
            self.assertEqual(schema_errors(schema), [], code)
            self.assertEqual(schema['additional'], 'reject')
        self.assertIn('possession', Market.objects.get(code='PK').listing_schema['fields'])
        self.assertIn('rera_permit_number', Market.objects.get(code='AE').listing_schema['fields'])
        self.assertIn('epc_rating', Market.objects.get(code='GB').listing_schema['fields'])
        self.assertEqual(Market.objects.get(code='US').listing_schema, {})


class ApiTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)

    def _create(self, **extra):
        body = {'title': 'H', 'city': 'Lahore', 'location': 'DHA', 'price': 1000, **extra}
        return self.c.post(URL, body, format='json')

    def test_create_validates_against_listing_market(self):
        r = self._create(attributes={'possession': 'available', 'corner': True})
        self.assertEqual(r.status_code, 201, r.data)
        r = self._create(attributes={'possession': 'soon', 'epc_rating': 'B'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.data['attributes']), {'possession', 'epc_rating'})
        r = self._create(country='GB', currency='GBP', attributes={'epc_rating': 'B', 'tenure': 'freehold'})
        self.assertEqual(r.status_code, 201, r.data)
        r = self._create(country='US', currency='USD', attributes={'anything': 'goes'})
        self.assertEqual(r.status_code, 201, r.data)            # US: no schema

    def test_update_rules(self):
        p = make_property(org=self.org, owner=self.dev, attributes={'legacy_key': 1})
        self.assertEqual(p.market_id, 'PK')
        # An unrelated PATCH does not re-validate stored (legacy) attributes.
        r = self.c.patch(f'{URL}{p.pk}/', {'title': 'T2'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        r = self.c.patch(f'{URL}{p.pk}/', {'attributes': {'legacy_key': 1}}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.c.patch(f'{URL}{p.pk}/', {'attributes': {'block': 'Phase 5'}}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        # Moving the listing to another market re-validates its attributes there.
        r = self.c.patch(f'{URL}{p.pk}/', {'country': 'GB'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('attributes', r.data)


class ModelCleanTest(TestCase):
    def test_full_clean_uses_market_schema(self):
        _, org = make_developer()
        p = Property.objects.get(pk=make_property(org=org, attributes={'possession': 'available'}).pk)
        p.full_clean()
        p.attributes = {'possession': 'never'}
        with self.assertRaises(ValidationError) as cm:
            p.full_clean()
        self.assertIn('attributes', cm.exception.message_dict)
        self.assertIsInstance(Property.attribute_schema_for('PK'), dict)
