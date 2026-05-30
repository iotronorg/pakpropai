"""Tests for password-based auth and OTPService purpose isolation."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.models import OTPCode, User
from apps.users.services import OTPService
from tests.factories import make_user

# MAX_ATTEMPTS is the hard limit checked by OTPCode.is_valid() (attempts < 5)
_MAX_ATTEMPTS = 5


class OTPServicePurposeTest(TestCase):

    def setUp(self):
        self.phone = '+923001111111'

    def test_issue_stores_purpose(self):
        otp = OTPService.issue(self.phone, purpose='password_reset')
        self.assertEqual(otp.purpose, 'password_reset')

    def test_verify_matches_purpose(self):
        otp = OTPService.issue(self.phone, purpose='password_reset')
        result = OTPService.verify(self.phone, otp.code, purpose='password_reset')
        self.assertEqual(result.phone, self.phone)

    def test_verify_rejects_wrong_purpose(self):
        otp = OTPService.issue(self.phone, purpose='password_reset')
        with self.assertRaises(ValueError) as ctx:
            OTPService.verify(self.phone, otp.code, purpose='otp_login')
        self.assertIn('No OTP found', str(ctx.exception))

    def test_verify_login_creates_user_if_new(self):
        otp = OTPService.issue(self.phone, purpose='otp_login')
        user = OTPService.verify_login(self.phone, otp.code)
        self.assertEqual(user.phone, self.phone)
        self.assertTrue(user.is_active)

    def test_verify_login_returns_existing_user(self):
        User.objects.create_user(phone=self.phone, role='agent')
        otp = OTPService.issue(self.phone, purpose='otp_login')
        user = OTPService.verify_login(self.phone, otp.code)
        self.assertEqual(user.phone, self.phone)

    def test_verify_rejects_expired_otp(self):
        otp = OTPService.issue(self.phone, purpose='otp_login')
        future = otp.expires_at + timedelta(seconds=1)
        with patch('django.utils.timezone.now', return_value=future):
            with self.assertRaises(ValueError) as ctx:
                OTPService.verify(self.phone, otp.code, purpose='otp_login')
        self.assertIn('expired', str(ctx.exception).lower())

    def test_verify_locks_after_too_many_attempts(self):
        otp = OTPService.issue(self.phone, purpose='otp_login')
        for _ in range(_MAX_ATTEMPTS):
            try:
                OTPService.verify(self.phone, 'wrong', purpose='otp_login')
            except ValueError:
                pass
        with self.assertRaises(ValueError):
            OTPService.verify(self.phone, otp.code, purpose='otp_login')

    @unittest.skip('Creates MAX_PER_HOUR DB rows — slow; rate-limit behaviour covered by service unit tests')
    def test_issue_rate_limit(self):
        for _ in range(OTPService.MAX_PER_HOUR):
            OTPService.issue(self.phone, purpose='otp_login')
        with self.assertRaises(ValueError):
            OTPService.issue(self.phone, purpose='otp_login')


class AuthSerializerTest(TestCase):

    def test_password_login_serializer_valid(self):
        from apps.users.serializers import PasswordLoginSerializer
        s = PasswordLoginSerializer(data={'identifier': '+923001234567', 'password': 'secret'})
        self.assertTrue(s.is_valid(), s.errors)

    def test_password_login_serializer_missing_fields(self):
        from apps.users.serializers import PasswordLoginSerializer
        s = PasswordLoginSerializer(data={})
        self.assertFalse(s.is_valid())
        self.assertIn('identifier', s.errors)
        self.assertIn('password', s.errors)

    def test_password_reset_request_invalid_phone(self):
        from apps.users.serializers import PasswordResetRequestSerializer
        s = PasswordResetRequestSerializer(data={'phone': 'not-a-phone'})
        self.assertFalse(s.is_valid())

    def test_password_reset_confirm_weak_password(self):
        from apps.users.serializers import PasswordResetConfirmSerializer
        s = PasswordResetConfirmSerializer(data={
            'phone': '+923001234567', 'code': '123456', 'new_password': '12345678'
        })
        # '12345678' is all-numeric — Django's NumericPasswordValidator should reject it
        self.assertFalse(s.is_valid())
        self.assertIn('new_password', s.errors)

    def test_password_change_weak_password(self):
        from apps.users.serializers import PasswordChangeSerializer
        s = PasswordChangeSerializer(data={'current_password': 'anything', 'new_password': 'password'})
        self.assertFalse(s.is_valid())
        self.assertIn('new_password', s.errors)

    def test_password_reset_request_valid_phone(self):
        from apps.users.serializers import PasswordResetRequestSerializer
        s = PasswordResetRequestSerializer(data={'phone': '+923001234567'})
        self.assertTrue(s.is_valid(), s.errors)

    def test_password_reset_confirm_strong_password(self):
        from apps.users.serializers import PasswordResetConfirmSerializer
        s = PasswordResetConfirmSerializer(data={
            'phone': '+923001234567', 'code': '123456', 'new_password': 'Str0ng!Pass99'
        })
        self.assertTrue(s.is_valid(), s.errors)

    def test_password_change_strong_password(self):
        from apps.users.serializers import PasswordChangeSerializer
        s = PasswordChangeSerializer(data={
            'current_password': 'anything', 'new_password': 'Str0ng!Pass99'
        })
        self.assertTrue(s.is_valid(), s.errors)

    def test_user_serializer_exposes_is_phone_verified(self):
        from apps.users.serializers import UserSerializer
        from tests.factories import make_user
        user = make_user(phone='+923009991111')
        s = UserSerializer(user)
        self.assertIn('is_phone_verified', s.data)

    def test_user_serializer_is_phone_verified_read_only(self):
        from apps.users.serializers import UserSerializer
        from tests.factories import make_user
        user = make_user(phone='+923009992222')
        # Attempting to set is_phone_verified via write should be ignored
        s = UserSerializer(user, data={'is_phone_verified': True}, partial=True)
        s.is_valid()
        # is_phone_verified should not appear in validated_data (it's read-only)
        self.assertNotIn('is_phone_verified', s.validated_data)


_AUTH = '/api/v1/auth'


def _make_user_with_password(phone, password, **kwargs):
    """Create a user and set a usable password (factory hardcodes 'pw')."""
    user = make_user(phone=phone, **kwargs)
    user.set_password(password)
    user.save(update_fields=['password'])
    return user


class AuthViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()

    # --- PasswordLoginView ---

    def test_password_login_by_phone(self):
        _make_user_with_password('+923001000001', 'SecurePass99!')
        resp = self.client.post(f'{_AUTH}/login/', {'identifier': '+923001000001', 'password': 'SecurePass99!'})
        self.assertEqual(resp.status_code, 200)

    def test_password_login_by_email(self):
        _make_user_with_password('+923001000002', 'SecurePass99!', email='test@example.com')
        resp = self.client.post(f'{_AUTH}/login/', {'identifier': 'test@example.com', 'password': 'SecurePass99!'})
        self.assertEqual(resp.status_code, 200)

    def test_password_login_wrong_password(self):
        _make_user_with_password('+923001000003', 'SecurePass99!')
        resp = self.client.post(f'{_AUTH}/login/', {'identifier': '+923001000003', 'password': 'wrong'})
        self.assertEqual(resp.status_code, 401)

    def test_password_login_user_not_found(self):
        resp = self.client.post(f'{_AUTH}/login/', {'identifier': '+923001000099', 'password': 'anything'})
        self.assertEqual(resp.status_code, 401)

    def test_password_login_no_password_set(self):
        user = make_user(phone='+923001000004')
        user.set_unusable_password()
        user.save()
        resp = self.client.post(f'{_AUTH}/login/', {'identifier': '+923001000004', 'password': 'anything'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('No password set', resp.data['detail'])

    def test_password_login_inactive_user(self):
        user = _make_user_with_password('+923001000005', 'SecurePass99!')
        user.is_active = False
        user.save()
        resp = self.client.post(f'{_AUTH}/login/', {'identifier': '+923001000005', 'password': 'SecurePass99!'})
        self.assertEqual(resp.status_code, 403)

    # --- PasswordResetRequestView ---

    def test_password_reset_request_known_phone(self):
        make_user(phone='+923001000010')
        resp = self.client.post(f'{_AUTH}/password/reset/request/', {'phone': '+923001000010'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(OTPCode.objects.filter(phone='+923001000010', purpose='password_reset').exists())

    def test_password_reset_request_unknown_phone(self):
        resp = self.client.post(f'{_AUTH}/password/reset/request/', {'phone': '+923001000099'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(OTPCode.objects.filter(phone='+923001000099').exists())

    # --- PasswordResetConfirmView ---

    def test_password_reset_confirm_success(self):
        user = make_user(phone='+923001000020')
        otp = OTPService.issue('+923001000020', purpose='password_reset')
        resp = self.client.post(f'{_AUTH}/password/reset/confirm/', {
            'phone': '+923001000020', 'code': otp.code, 'new_password': 'NewSecure99!'
        })
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('NewSecure99!'))

    def test_password_reset_confirm_wrong_otp(self):
        make_user(phone='+923001000021')
        OTPService.issue('+923001000021', purpose='password_reset')
        resp = self.client.post(f'{_AUTH}/password/reset/confirm/', {
            'phone': '+923001000021', 'code': '000000', 'new_password': 'NewSecure99!'
        })
        self.assertEqual(resp.status_code, 400)

    def test_password_reset_confirm_purpose_mismatch(self):
        make_user(phone='+923001000022')
        otp = OTPService.issue('+923001000022', purpose='otp_login')
        resp = self.client.post(f'{_AUTH}/password/reset/confirm/', {
            'phone': '+923001000022', 'code': otp.code, 'new_password': 'NewSecure99!'
        })
        self.assertEqual(resp.status_code, 400)

    # --- PasswordChangeView ---

    def test_password_change_success(self):
        user = _make_user_with_password('+923001000030', 'OldPass99!')
        self.client.force_authenticate(user=user)
        resp = self.client.post(f'{_AUTH}/password/change/', {
            'current_password': 'OldPass99!', 'new_password': 'NewPass99!'
        })
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('NewPass99!'))

    def test_password_change_wrong_current(self):
        user = _make_user_with_password('+923001000031', 'OldPass99!')
        self.client.force_authenticate(user=user)
        resp = self.client.post(f'{_AUTH}/password/change/', {
            'current_password': 'wrong', 'new_password': 'NewPass99!'
        })
        self.assertEqual(resp.status_code, 400)

    def test_password_change_unauthenticated(self):
        resp = self.client.post(f'{_AUTH}/password/change/', {
            'current_password': 'anything', 'new_password': 'NewPass99!'
        })
        self.assertEqual(resp.status_code, 401)

    # --- RegistrationOTPVerifyView ---

    def test_registration_otp_verify_success(self):
        user = make_user(phone='+923001000040')
        user.is_phone_verified = False
        user.save()
        otp = OTPService.issue('+923001000040', purpose='registration_verify')
        resp = self.client.post(f'{_AUTH}/registration/verify-otp/', {
            'phone': '+923001000040', 'code': otp.code
        })
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_phone_verified)

    def test_registration_otp_verify_invalid_code(self):
        make_user(phone='+923001000041')
        OTPService.issue('+923001000041', purpose='registration_verify')
        resp = self.client.post(f'{_AUTH}/registration/verify-otp/', {
            'phone': '+923001000041', 'code': '000000'
        })
        self.assertEqual(resp.status_code, 400)

    def test_registration_otp_verify_user_not_found(self):
        """OTP verify for a phone with no user record returns 400."""
        otp = OTPService.issue('+923001000050', purpose='registration_verify')
        resp = self.client.post(f'{_AUTH}/registration/verify-otp/', {
            'phone': '+923001000050', 'code': otp.code
        })
        self.assertEqual(resp.status_code, 400)

    def test_password_change_old_tokens_blacklisted(self):
        """After password change, outstanding refresh tokens are blacklisted."""
        from rest_framework_simplejwt.tokens import RefreshToken
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        user = _make_user_with_password('+923001000051', 'OldPass99!')
        refresh = RefreshToken.for_user(user)
        self.client.force_authenticate(user=user)
        self.client.post(f'{_AUTH}/password/change/', {
            'current_password': 'OldPass99!', 'new_password': 'NewPass99!'
        })
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=str(refresh['jti'])).exists())


class AgentRegistrationOTPTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.register_url = '/api/v1/agents/register/'

    def _registration_payload(self, phone='+923002000001'):
        return {
            'phone': phone,
            'name': 'Test Agent',
            'password': 'SecurePass99!',
        }

    @patch('apps.notifications.tasks.send_otp_async.delay', return_value=None)
    def test_registration_issues_otp(self, mock_send):
        resp = self.client.post(self.register_url, self._registration_payload())
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            OTPCode.objects.filter(phone='+923002000001', purpose='registration_verify').exists()
        )

    @patch('apps.notifications.tasks.send_otp_async.delay', return_value=None)
    def test_registration_response_includes_otp_required(self, mock_send):
        resp = self.client.post(self.register_url, self._registration_payload('+923002000002'))
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data.get('otp_required'))

    @patch('apps.notifications.tasks.send_otp_async.delay', return_value=None)
    def test_registration_password_is_hashed(self, mock_send):
        from django.contrib.auth import get_user_model
        resp = self.client.post(self.register_url, self._registration_payload('+923002000003'))
        self.assertEqual(resp.status_code, 201)
        UserModel = get_user_model()
        user = UserModel.objects.get(phone='+923002000003')
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password('SecurePass99!'))

    def test_registration_weak_password_rejected(self):
        payload = self._registration_payload('+923002000004')
        payload['password'] = '12345678'  # all-numeric — Django's NumericPasswordValidator rejects
        resp = self.client.post(self.register_url, payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('password', resp.data)


class TokenRefreshRotationTest(TestCase):
    """CookieTokenRefreshView must rotate the refresh token (A10-SEC-1 fix)."""

    def setUp(self):
        self.client = APIClient()
        self.user = make_user(phone='+923005000001', role='developer')
        from rest_framework_simplejwt.tokens import RefreshToken as _RT
        refresh = _RT.for_user(self.user)
        self.raw_refresh = str(refresh)
        self.raw_access = str(refresh.access_token)

    def _post_refresh(self):
        self.client.cookies['refresh_token'] = self.raw_refresh
        return self.client.post('/api/v1/auth/token/refresh/')

    def test_refresh_returns_200(self):
        resp = self._post_refresh()
        self.assertEqual(resp.status_code, 200)

    def test_refresh_sets_new_access_cookie(self):
        resp = self._post_refresh()
        self.assertIn('access_token', resp.cookies)

    def test_refresh_sets_new_refresh_cookie(self):
        """After refresh, a new refresh_token cookie must be issued (rotation)."""
        resp = self._post_refresh()
        self.assertIn('refresh_token', resp.cookies)
        new_raw = resp.cookies['refresh_token'].value
        self.assertNotEqual(new_raw, self.raw_refresh,
                            "Refresh token must change after rotation — old token reused")

    def test_old_refresh_token_is_blacklisted(self):
        """Old refresh token must be blacklisted after rotation."""
        import base64, json
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        self._post_refresh()
        # Decode JTI from raw JWT without calling RefreshToken() — that would
        # raise TokenError because the token is now blacklisted.
        parts = self.raw_refresh.split('.')
        payload = json.loads(base64.b64decode(parts[1] + '=='))
        old_jti = payload['jti']
        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=old_jti).exists(),
            "Old refresh token must be blacklisted after CookieTokenRefreshView rotation",
        )

    def test_old_refresh_token_rejected_after_rotation(self):
        """Re-using the old refresh token after rotation must return 401."""
        self._post_refresh()
        # Try the old refresh token again
        resp2 = self._post_refresh()
        self.assertEqual(resp2.status_code, 401,
                         "Re-using a rotated-away refresh token must be rejected")


class UserPhoneValidatorTest(TestCase):
    """User model phone validator enforces E.164 (A10-GLOBAL-4 fix).

    The validator must require the + prefix so only E.164-formatted numbers
    are stored in the DB — bare digits without + must be rejected at model level.
    """

    def _save(self, phone):
        from apps.users.models import User
        user = User(phone=phone, role='client')
        user.set_unusable_password()   # prevent blank-password validation error
        user.full_clean()              # triggers model-level validators incl. phone

    def test_valid_e164_accepted(self):
        self._save('+923001234567')   # PK
        self._save('+971501234567')   # AE
        self._save('+447911123456')   # GB
        self._save('+12125551234')    # US
        self._save('+1234567')        # minimum length (7 digits after +)

    def test_missing_plus_rejected(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError, msg='923001234567 (no +) must be rejected'):
            self._save('923001234567')

    def test_bare_digits_rejected(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self._save('03001234567')   # PK local format without +

    def test_too_short_rejected(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self._save('+12345')   # only 5 digits — below E.164 minimum

    def test_too_long_rejected(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self._save('+1234567890123456')   # 16 digits — above E.164 maximum

    def test_error_message_mentions_e164(self):
        from django.core.exceptions import ValidationError
        try:
            self._save('0923001234567')
        except ValidationError as exc:
            msg = str(exc)
            self.assertIn('E.164', msg)
        else:
            self.fail('ValidationError not raised for number without +')
