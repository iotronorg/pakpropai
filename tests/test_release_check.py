"""T-6040 — ``manage.py check_release``: frozen modules off, required production settings present.

MVP_SCOPE §7 "Feature flags verified: frozen modules off in production". The deploy script runs the
command before switching (tests/test_deploy_pipeline.py covers the script side).
"""
import json
import os
from io import StringIO
from unittest import mock

from cryptography.fernet import Fernet
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.config.models import SystemConfig
from apps.core import release_checks as rc
from apps.inventory.models import ExternalPlatformConnection
from apps.organizations.models import OrganizationConfig
from tests.factories import make_org

SECRET = 'S3cr3t-' + 'x9Q2w' * 10
ENC_KEY = Fernet.generate_key().decode()
TOKEN = 'metrics-token-0123456789abcdef'

# A configuration that passes: prod.py's security settings plus the env values the operator sets.
PROD = dict(
    ENVIRONMENT='production', DEBUG=False,
    SECRET_KEY=SECRET, ALLOWED_HOSTS=['api.realtron.ai'],
    FIELD_ENCRYPTION_KEYS=[ENC_KEY], FIELD_ENCRYPTION_ALLOW_DERIVED_KEY=False,
    FIELD_HASH_KEY='hash-key-' + 'a' * 32, AUDIT_CHAIN_KEY='audit-key-' + 'b' * 32,
    FRONTEND_URL='https://app.realtron.ai', BASE_URL='https://api.realtron.ai',
    SECURE_SSL_REDIRECT=True, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
    SECURE_HSTS_SECONDS=31536000, SECURE_HSTS_INCLUDE_SUBDOMAINS=True, SECURE_HSTS_PRELOAD=True,
    PROMETHEUS_METRICS_TOKEN=TOKEN, PROMETHEUS_ALLOWED_IPS=['127.0.0.1'],
    DEFAULT_MARKET='PK', AI_BACKEND='gemini', AI_PROVIDER_ORDER=['gemini'],
    GEMINI_API_KEY='gemini-key-value', WA_ACCESS_TOKEN='wa-token-value', WA_PHONE_NUMBER_ID='123',
    WA_VERIFY_TOKEN='verify-value', GEOCODING_ENABLED=False, OPENAPI_SCHEMA_PUBLIC=False,
    EMAIL_HOST='smtp.example.com', SENTRY_DSN='https://k@sentry.example.com/1',
    CLOUDINARY_STORAGE={'CLOUD_NAME': 'c', 'API_KEY': 'k', 'API_SECRET': 's'}, APP_VERSION='0123456789ab',
)


def codes(findings, level=None):
    return {f.code for f in findings if level is None or f.level == level}


@override_settings(**PROD)
@mock.patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'config.settings.prod'})
class ReleaseCheckTest(TestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command('check_release', *args, stdout=out)
        return out.getvalue()

    def test_a_good_configuration_passes(self):
        findings = rc.run_checks()
        self.assertEqual([f for f in findings if f.level != rc.OK], [])
        self.assertIn('frozen.feature_deal_lock', codes(findings, rc.OK))
        self.assertIn('db.migrations', codes(findings, rc.OK))
        self.assertIn('0 failed, 0 warnings', self.run_cmd())

    def test_frozen_module_on_fails_in_production(self):
        for key in rc.FROZEN_FLAGS:
            with self.subTest(key=key):
                SystemConfig.objects.update_or_create(key=key, defaults={'value': 'true'})
                with self.assertRaises(CommandError):
                    self.run_cmd()
                self.assertIn(f'frozen.{key}', codes(rc.run_checks(), rc.FAIL))
                SystemConfig.objects.filter(key=key).update(value='false')
        self.run_cmd()

    def test_odd_values_count_as_on(self):
        SystemConfig.objects.create(key='feature_voice_calls', value='True ')
        self.assertIn('frozen.feature_voice_calls', codes(rc.run_checks(), rc.FAIL))

    def test_frozen_module_on_only_warns_on_staging(self):
        SystemConfig.objects.create(key='feature_deal_lock', value='true')
        findings = rc.run_checks('staging')
        self.assertIn('frozen.feature_deal_lock', codes(findings, rc.WARN))
        self.run_cmd('--environment', 'staging')
        with self.assertRaises(CommandError):
            self.run_cmd('--environment', 'staging', '--strict')

    def test_review_flags_and_org_overrides_warn(self):
        SystemConfig.objects.create(key='semantic_cache_enabled', value='true')
        org = make_org()
        OrganizationConfig.objects.create(organization=org, key='feature_deal_lock', value='true')
        findings = rc.run_checks()
        self.assertEqual(codes(findings, rc.FAIL), set())
        self.assertTrue({'review.semantic_cache_enabled', 'frozen.org.feature_deal_lock'}
                        <= codes(findings, rc.WARN))

    def test_partner_feeds_are_listed(self):
        org = make_org()
        ExternalPlatformConnection.objects.create(org=org, platform='zameen')
        ExternalPlatformConnection.objects.create(org=org, platform='bayut', is_active=False)
        ExternalPlatformConnection.objects.create(org=org, platform='custom', base_url='https://x.example')
        warns = codes(rc.run_checks(), rc.WARN)
        self.assertIn('dormant.inventory.zameen', warns)
        self.assertNotIn('dormant.inventory.bayut', warns)
        self.assertNotIn('dormant.inventory.custom', warns)

    def test_bad_settings_fail(self):
        cases = {
            'settings.DEBUG': {'DEBUG': True},
            'settings.ENVIRONMENT': {'ENVIRONMENT': 'development'},
            'settings.SECRET_KEY': {'SECRET_KEY': 'ci-only-not-for-production-' + 'z' * 40},
            'settings.ALLOWED_HOSTS': {'ALLOWED_HOSTS': ['*']},
            'settings.FIELD_ENCRYPTION_KEYS': {'FIELD_ENCRYPTION_KEYS': ['not-a-fernet-key']},
            'settings.FIELD_ENCRYPTION_ALLOW_DERIVED_KEY': {'FIELD_ENCRYPTION_ALLOW_DERIVED_KEY': True},
            'settings.FIELD_HASH_KEY': {'FIELD_HASH_KEY': ''},
            'settings.AUDIT_CHAIN_KEY': {'AUDIT_CHAIN_KEY': SECRET},
            'settings.FRONTEND_URL': {'FRONTEND_URL': 'http://localhost:3000'},
            'settings.BASE_URL': {'BASE_URL': 'http://api.realtron.ai'},
            'settings.https': {'SESSION_COOKIE_SECURE': False},
            'settings.PROMETHEUS_METRICS_TOKEN': {'PROMETHEUS_METRICS_TOKEN': 'short'},
            'settings.PROMETHEUS_ALLOWED_IPS': {'PROMETHEUS_ALLOWED_IPS': ['0.0.0.0']},
            'settings.DEFAULT_MARKET': {'DEFAULT_MARKET': 'ZZ'},
            'ai.gemini': {'GEMINI_API_KEY': ''},
            'ai.openai': {'AI_PROVIDER_ORDER': ['gemini', 'openai'], 'AI_OPENAI_API_KEY': ''},
            'ai.local': {'AI_PROVIDER_ORDER': ['local'], 'OLLAMA_BASE_URL': 'http://localhost:11434'},
            'ai.providers': {'AI_PROVIDER_ORDER': ['gemini', 'claud']},
            'config.required': {'WA_ACCESS_TOKEN': ''},
            'settings.GOOGLE_MAPS_API_KEY': {'GEOCODING_ENABLED': True, 'GOOGLE_MAPS_API_KEY': ''},
        }
        for code, override in cases.items():
            with self.subTest(code=code), self.settings(**override):
                self.assertIn(code, codes(rc.check_settings('production'), rc.FAIL))
        with mock.patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'config.settings.dev'}):
            self.assertIn('settings.module', codes(rc.check_settings('production'), rc.FAIL))

    def test_optional_settings_only_warn(self):
        with self.settings(EMAIL_HOST='', SENTRY_DSN='', APP_VERSION='dev', OPENAPI_SCHEMA_PUBLIC=True,
                           CLOUDINARY_STORAGE={'CLOUD_NAME': '', 'API_KEY': '', 'API_SECRET': ''}):
            findings = rc.check_settings('production')
            self.assertEqual(codes(findings, rc.FAIL), set())
            self.assertEqual({'settings.EMAIL_HOST', 'settings.SENTRY_DSN', 'settings.APP_VERSION',
                              'settings.OPENAPI_SCHEMA_PUBLIC', 'settings.CLOUDINARY'},
                             codes(findings, rc.WARN))
            self.assertNotIn('settings.OPENAPI_SCHEMA_PUBLIC', codes(rc.check_settings('staging')))

    def test_regional_clusters_warn(self):
        with mock.patch.dict(rc.REGIONAL_DATABASES, {'default': 'EU_DATABASE_URL'}):
            self.assertIn('dormant.regional_db', codes(rc.check_dormant_modules(), rc.WARN))

    def test_output_never_contains_secret_values(self):
        with self.settings(PROMETHEUS_METRICS_TOKEN='tiny', AUDIT_CHAIN_KEY=SECRET, DEBUG=True):
            out = StringIO()
            with self.assertRaises(CommandError):
                call_command('check_release', '--json', '--verbose-ok', stdout=out)
        text = out.getvalue()
        for value in (SECRET, ENC_KEY, 'tiny', 'gemini-key-value', 'wa-token-value', 'hash-key-'):
            self.assertNotIn(value, text)
        body = json.loads(text)
        self.assertFalse(body['ok'])
        self.assertGreaterEqual(body['failed'], 3)

    def test_json_and_strict(self):
        body = json.loads(self.run_cmd('--json'))
        self.assertEqual((body['ok'], body['failed'], body['warnings'], body['findings']), (True, 0, 0, []))
        with self.settings(SENTRY_DSN=''):
            self.run_cmd()
            with self.assertRaises(CommandError):
                self.run_cmd('--strict')

    def test_pending_migrations_fail(self):
        fake = mock.Mock(app_label='core', name='9999_x')
        fake.name = '9999_x'
        with mock.patch('django.db.migrations.executor.MigrationExecutor.migration_plan',
                        return_value=[(fake, False)]):
            self.assertIn('db.migrations', codes(rc.check_migrations(), rc.FAIL))

    def test_a_broken_group_is_reported_not_raised(self):
        with mock.patch.object(rc, 'check_frozen_flags', side_effect=RuntimeError('secret detail')):
            findings = rc.run_checks()
        self.assertIn('frozen.error', codes(findings, rc.FAIL))
        self.assertIn('settings.DEBUG', codes(findings, rc.OK))
        self.assertNotIn('secret detail', ' '.join(f.message for f in findings))
