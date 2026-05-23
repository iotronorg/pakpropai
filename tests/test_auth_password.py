"""Tests for password-based auth and OTPService purpose isolation."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.users.models import OTPCode, User
from apps.users.services import OTPService

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
