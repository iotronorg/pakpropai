"""
T-0010 / ADR-008 — field-level encryption of secrets and national IDs.
"""
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core import crypto
from apps.whatsapp.models import OrgWhatsAppConfig
from tests.factories import make_developer, make_user


def _raw(table, column, pk):
    with connection.cursor() as cur:
        cur.execute(f'SELECT "{column}" FROM "{table}" WHERE id = %s', [pk])
        return cur.fetchone()[0]


class CryptoPrimitivesTest(TestCase):

    def test_roundtrip_and_prefix(self):
        token = crypto.encrypt_str('s3cret')
        self.assertTrue(token.startswith('enc1:'))
        self.assertNotIn('s3cret', token)
        self.assertEqual(crypto.decrypt_str(token), 's3cret')

    def test_randomised_ciphertext(self):
        self.assertNotEqual(crypto.encrypt_str('x'), crypto.encrypt_str('x'))

    def test_empty_and_legacy_pass_through(self):
        self.assertEqual(crypto.encrypt_str(''), '')
        self.assertIsNone(crypto.encrypt_str(None))
        self.assertEqual(crypto.decrypt_str('legacy-plaintext'), 'legacy-plaintext')

    def test_no_double_encryption(self):
        token = crypto.encrypt_str('v')
        self.assertEqual(crypto.encrypt_str(token), token)

    @override_settings(FIELD_ENCRYPTION_KEYS=[], FIELD_ENCRYPTION_ALLOW_DERIVED_KEY=False)
    def test_missing_key_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            crypto.encrypt_str('v')
        from apps.core.checks import field_encryption_key_check
        self.assertEqual(field_encryption_key_check(None)[0].id, 'core.E002')

    def test_key_rotation(self):
        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        with override_settings(FIELD_ENCRYPTION_KEYS=[old]):
            token = crypto.encrypt_str('rotate-me')
        with override_settings(FIELD_ENCRYPTION_KEYS=[new, old]):
            self.assertEqual(crypto.decrypt_str(token), 'rotate-me')
            rotated = crypto.rotate_str(token)
        with override_settings(FIELD_ENCRYPTION_KEYS=[new]):
            self.assertEqual(crypto.decrypt_str(rotated), 'rotate-me')

    def test_blind_index_deterministic(self):
        self.assertEqual(crypto.blind_index('abc'), crypto.blind_index('abc'))
        self.assertNotEqual(crypto.blind_index('abc'), crypto.blind_index('abd'))
        self.assertEqual(crypto.blind_index(''), '')


class EncryptedAtRestTest(TestCase):

    def test_user_cnic_encrypted_in_db(self):
        user = make_user(cnic='35202-1234567-1')
        raw = _raw('users', 'cnic', user.pk)
        self.assertTrue(raw.startswith('enc1:'))
        user.refresh_from_db()
        self.assertEqual(user.cnic, '35202-1234567-1')

    def test_whatsapp_secrets_encrypted_and_lookup_works(self):
        _, org = make_developer()
        cfg = OrgWhatsAppConfig.objects.create(
            organization=org, access_token='EAAG-token', app_secret='appsecret',
            verify_token='verify-me', is_active=True,
        )
        for col in ('access_token', 'app_secret', 'verify_token'):
            self.assertTrue(_raw('org_whatsapp_configs', col, cfg.pk).startswith('enc1:'), col)
        self.assertEqual(OrgWhatsAppConfig.find_by_verify_token('verify-me', is_active=True), cfg)
        self.assertIsNone(OrgWhatsAppConfig.find_by_verify_token('wrong'))

    def test_webhook_verify_with_org_token(self):
        _, org = make_developer()
        OrgWhatsAppConfig.objects.create(organization=org, verify_token='org-verify', is_active=True)
        resp = APIClient().get('/api/v1/whatsapp/webhook/', {
            'hub.mode': 'subscribe', 'hub.verify_token': 'org-verify', 'hub.challenge': '42',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'42')
        bad = APIClient().get('/api/v1/whatsapp/webhook/', {
            'hub.mode': 'subscribe', 'hub.verify_token': 'nope', 'hub.challenge': '42',
        })
        self.assertEqual(bad.status_code, 403)

    def test_legacy_plaintext_row_still_readable(self):
        user = make_user()
        with connection.cursor() as cur:
            cur.execute('UPDATE users SET cnic = %s WHERE id = %s', ['legacy-cnic', user.pk])
        user.refresh_from_db()
        self.assertEqual(user.cnic, 'legacy-cnic')

    def test_system_config_secret_encrypted_in_db_and_cache(self):
        from apps.config.models import SystemConfig
        from apps.config.services import CACHE_PREFIX, SystemConfigService
        SystemConfigService.set('gemini_api_key', 'AIza-secret')
        obj = SystemConfig.objects.get(key='gemini_api_key')
        self.assertTrue(_raw('system_config', 'value', obj.pk).startswith('enc1:'))
        self.assertEqual(SystemConfigService.get('gemini_api_key'), 'AIza-secret')
        self.assertTrue(cache.get(f'{CACHE_PREFIX}gemini_api_key').startswith('enc1:'))
        self.assertEqual(SystemConfigService.get('gemini_api_key'), 'AIza-secret')  # cached path


class MigrationHelpersTest(TestCase):

    def test_encrypt_then_decrypt_columns(self):
        user = make_user()
        with connection.cursor() as cur:
            cur.execute('UPDATE users SET cnic = %s WHERE id = %s', ['plain-1', user.pk])

        class _SE:
            pass
        se = _SE()
        se.connection = connection
        se.quote_name = connection.ops.quote_name

        crypto.encrypt_columns('users', ['cnic'])(None, se)
        self.assertTrue(_raw('users', 'cnic', user.pk).startswith('enc1:'))
        crypto.encrypt_columns('users', ['cnic'])(None, se)   # idempotent
        user.refresh_from_db()
        self.assertEqual(user.cnic, 'plain-1')

        crypto.decrypt_columns('users', ['cnic'])(None, se)
        self.assertEqual(_raw('users', 'cnic', user.pk), 'plain-1')
