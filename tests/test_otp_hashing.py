"""T-1010 / KI-016: OTP codes are stored only as a keyed HMAC, never in plaintext."""
from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.users.models import OTPCode
from apps.users.services import OTPService

PHONE = '+923001234567'


class OTPStorageTest(TestCase):

    def test_plaintext_not_persisted(self):
        otp = OTPService.issue(PHONE)
        self.assertRegex(otp.code, r'^\d{6}$')
        self.assertNotIn('code', [f.name for f in OTPCode._meta.get_fields()])

        with connection.cursor() as cur:
            cur.execute('SELECT * FROM otp_codes WHERE id = %s', [otp.pk])
            row = cur.fetchone()
            cols = [c.name for c in cur.description]
        self.assertNotIn('code', cols)
        self.assertNotIn(otp.code, [str(v) for v in row])
        stored = dict(zip(cols, row))['code_hash']
        self.assertRegex(stored, r'^[0-9a-f]{64}$')

    def test_reloaded_row_has_no_plaintext(self):
        otp = OTPService.issue(PHONE)
        fresh = OTPCode.objects.get(pk=otp.pk)
        self.assertFalse(hasattr(fresh, 'code'))
        self.assertTrue(fresh.check_code(otp.code))

    def test_hash_bound_to_phone_and_purpose(self):
        h = OTPCode.hash_code(PHONE, 'otp_login', '123456')
        self.assertEqual(h, OTPCode.hash_code(PHONE, 'otp_login', '123456'))
        self.assertNotEqual(h, OTPCode.hash_code('+923009999999', 'otp_login', '123456'))
        self.assertNotEqual(h, OTPCode.hash_code(PHONE, 'password_reset', '123456'))
        self.assertNotEqual(h, OTPCode.hash_code(PHONE, 'otp_login', '123457'))

    def test_hash_is_keyed(self):
        h = OTPCode.hash_code(PHONE, 'otp_login', '123456')
        with override_settings(FIELD_HASH_KEY='another-key'):
            self.assertNotEqual(h, OTPCode.hash_code(PHONE, 'otp_login', '123456'))

    def test_check_code_rejects_empty_and_wrong(self):
        otp = OTPService.issue(PHONE)
        self.assertFalse(otp.check_code(''))
        self.assertFalse(otp.check_code(None))
        wrong = '000000' if otp.code != '000000' else '111111'
        self.assertFalse(otp.check_code(wrong))
        self.assertFalse(OTPCode(phone=PHONE, expires_at=timezone.now()).check_code('123456'))

    def test_verify_round_trip_and_single_use(self):
        otp = OTPService.issue(PHONE)
        OTPService.verify(PHONE, otp.code)
        with self.assertRaises(ValueError):
            OTPService.verify(PHONE, otp.code)

    def test_wrong_code_increments_attempts(self):
        otp = OTPService.issue(PHONE)
        with self.assertRaises(ValueError):
            OTPService.verify(PHONE, 'nope')
        otp.refresh_from_db()
        self.assertEqual(otp.attempts, 1)


class OTPEndpointsTest(TestCase):

    @patch('apps.notifications.tasks.send_otp_async.delay')
    def test_send_then_verify_via_api(self, mock_delay):
        client = APIClient()
        resp = client.post('/api/v1/auth/otp/send/', {'phone': PHONE}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        mock_delay.assert_called_once()
        sent_code = mock_delay.call_args[0][1]
        self.assertNotIn(sent_code, OTPCode.objects.get(phone=PHONE).code_hash)

        resp = client.post('/api/v1/auth/otp/verify/', {'phone': PHONE, 'code': sent_code}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)


class OTPHashMigrationTest(TransactionTestCase):
    """Forward hashes existing plaintext codes; reverse invalidates them."""

    before = [('users', '0012_encrypt_sensitive_fields')]
    after = [('users', '0013_otp_code_hash')]

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self._migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_forward_and_reverse(self):
        old_apps = self._migrate(self.before)
        Old = old_apps.get_model('users', 'OTPCode')
        expires = timezone.now() + timedelta(minutes=5)
        row = Old.objects.create(phone=PHONE, code='654321', purpose='otp_login', expires_at=expires)

        self._migrate(self.after)
        migrated = OTPCode.objects.get(pk=row.pk)
        self.assertTrue(migrated.check_code('654321'))
        self.assertFalse(migrated.is_used)

        old_apps = self._migrate(self.before)
        reverted = old_apps.get_model('users', 'OTPCode').objects.get(pk=row.pk)
        self.assertEqual(reverted.code, '')
        self.assertTrue(reverted.is_used)
