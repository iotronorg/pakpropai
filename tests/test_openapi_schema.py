"""
T-1090a / ADR-016 — OpenAPI schema.

Covers:
  * the schema builds, is valid OpenAPI 3.1 and documents the real auth schemes
  * the error/warning budget only goes down (ratchet): a new endpoint that the
    generator cannot describe fails here, so the debt does not grow
  * who may download it from the running service
"""
from django.test import TestCase, override_settings
from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema
from rest_framework.test import APIClient

from tests.factories import make_client, make_user

SCHEMA_URL = '/api/v1/schema/'

# Unique generator messages at introduction (session 6). Lower these when
# views are annotated; never raise them.
ERROR_BUDGET = 98       # almost all: APIView without serializer_class (T-1091: verification, users, compliance, reports)
WARNING_BUDGET = 22     # T-1091: users list/detail split, type hints and operation ids


def _build_schema():
    GENERATOR_STATS.reset()
    with GENERATOR_STATS.silence():
        schema = SchemaGenerator().get_schema(request=None, public=True)
    errors, warnings = dict(GENERATOR_STATS._error_cache), dict(GENERATOR_STATS._warn_cache)
    GENERATOR_STATS.reset()
    return schema, errors, warnings


class SchemaGenerationTest(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema, cls.errors, cls.warnings = _build_schema()

    def test_schema_is_valid_openapi_31(self):
        self.assertEqual(self.schema['openapi'], '3.1.0')
        validate_schema(self.schema)  # raises on an invalid document

    def test_core_endpoints_are_documented(self):
        paths = self.schema['paths']
        for path in ('/api/v1/properties/', '/api/v1/properties/{id}/', '/api/v1/leads/'):
            with self.subTest(path=path):
                self.assertIn(path, paths)
        self.assertNotIn(SCHEMA_URL, paths)  # the schema endpoint itself is excluded

    def test_auth_schemes_are_documented(self):
        schemes = self.schema['components']['securitySchemes']
        self.assertEqual(schemes['jwtBearer'], {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT'})
        self.assertEqual(schemes['jwtCookie']['in'], 'cookie')
        self.assertEqual(schemes['orgApiKey']['name'], 'X-API-Key')

    def test_error_envelope_component_is_documented(self):
        env = self.schema['components']['schemas']['ErrorEnvelope']
        self.assertEqual(set(env['properties']), {'error_info', 'request_id'})

    def test_property_search_params_are_documented(self):
        # The list endpoint's OrderingFilter is picked up from filter_backends.
        params = {p['name'] for p in self.schema['paths']['/api/v1/properties/']['get']['parameters']}
        self.assertIn('ordering', params)

    def test_t1091_endpoints_are_documented(self):
        paths = self.schema['paths']
        for path in ('/api/v1/verification/queue/', '/api/v1/verification/fraud/blacklist/',
                     '/api/v1/auth/otp/verify/', '/api/v1/auth/login/', '/api/v1/auth/users/{id}/',
                     '/api/v1/compliance/screenings/', '/api/v1/compliance/rtbf/{request_id}/',
                     '/api/v1/reports/leads/', '/api/v1/reports/generate/', '/api/v1/reports/monthly/'):
            self.assertIn(path, paths)
        self.assertNotIn('/api/v1/verification/webhook/jumio/', paths)           # provider callbacks stay out
        self.assertEqual(set(paths['/api/v1/auth/users/']) - {'parameters'}, {'get', 'post'})
        self.assertEqual(set(paths['/api/v1/auth/users/{id}/']) - {'parameters'}, {'patch', 'delete'})

    def test_generator_error_budget_does_not_grow(self):
        self.assertLessEqual(
            len(self.errors), ERROR_BUDGET,
            'New schema generation errors. Give the view a serializer_class or '
            '@extend_schema. New messages:\n' + '\n'.join(sorted(self.errors)[:20]),
        )

    def test_generator_warning_budget_does_not_grow(self):
        self.assertLessEqual(len(self.warnings), WARNING_BUDGET, '\n'.join(sorted(self.warnings)[:20]))


class SchemaAccessTest(TestCase):

    def setUp(self):
        self.api = APIClient()

    def test_anonymous_is_refused(self):
        self.assertIn(self.api.get(SCHEMA_URL).status_code, (401, 403))

    def test_regular_user_is_refused(self):
        self.api.force_authenticate(make_client())
        self.assertEqual(self.api.get(SCHEMA_URL).status_code, 403)

    def test_platform_admin_can_download(self):
        self.api.force_authenticate(make_user(role='admin'))
        resp = self.api.get(SCHEMA_URL, {'format': 'json'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['openapi'], '3.1.0')

    def test_staff_can_download_yaml(self):
        self.api.force_authenticate(make_client(is_staff=True))
        resp = self.api.get(SCHEMA_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('openapi', resp['Content-Type'])

    @override_settings(OPENAPI_SCHEMA_PUBLIC=True)
    def test_public_flag_opens_it(self):
        self.assertEqual(self.api.get(SCHEMA_URL, {'format': 'json'}).status_code, 200)
