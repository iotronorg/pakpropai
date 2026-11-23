"""
T-1011 — MFA (TOTP + recovery codes), ADR-026.

RFC 6238 vectors, enrolment (signed in, and via an enrolment challenge when
enforced), the login second step on every token-issuing path, replay and
lockout, single-use challenges, token claim enforcement (requests and
refresh), disable / admin reset with audit, the Django admin login form and
secret encryption at rest.
"""
from unittest.mock import MagicMock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.core.models import AuditLog
from apps.users import mfa
from apps.users.models import MFADevice, User
from tests.factories import make_developer, make_user

A = '/api/v1/auth/'
RFC_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'          # ASCII "12345678901234567890"


def _bearer(response):
    """Client using the access token a response set as a cookie (the cookie path needs CSRF)."""
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {response.cookies['access_token'].value}")
    return c


def _code(user, ahead=0):
    secret = MFADevice.objects.get(user=user).secret
    return mfa.totp_at(secret, mfa.current_step() + ahead)


class TotpTest(TestCase):
    def test_rfc6238_vectors_and_window(self):
        # RFC 6238 appendix B (SHA-1, 8 digits) → the last 6 digits.
        self.assertEqual(mfa.totp_at(RFC_SECRET, 59 // 30), '287082')
        self.assertEqual(mfa.totp_at(RFC_SECRET, 1111111109 // 30), '081804')
        self.assertEqual(mfa.totp_at(RFC_SECRET, 2000000000 // 30), '279037')
        now = 1111111109
        self.assertEqual(mfa.match_step(RFC_SECRET, '081804', now=now), now // 30)
        self.assertEqual(mfa.match_step(RFC_SECRET, '081804', now=now + 30), now // 30)   # ±1 step
        self.assertIsNone(mfa.match_step(RFC_SECRET, '081804', now=now + 90))
        self.assertIsNone(mfa.match_step(RFC_SECRET, 'abcdef', now=now))
        self.assertIn('issuer=RealTron', mfa.otpauth_uri(RFC_SECRET, '+923001234567'))


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.c = APIClient()

    def _login(self, user, client=None):
        return (client or self.c).post(A + 'login/', {'identifier': user.phone, 'password': 'pw'},
                                       format='json')

    def _enrol(self, user):
        c = APIClient()
        c.force_authenticate(user)
        self.assertEqual(c.post(A + 'mfa/totp/setup/').status_code, 200)
        r = c.post(A + 'mfa/totp/confirm/', {'code': _code(user)}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        user.refresh_from_db()
        # The confirm code used the current step: later codes must be newer.
        MFADevice.objects.filter(user=user).update(last_used_step=mfa.current_step() - 2)
        return r.data['recovery_codes']


class EnrolmentTest(_Base):
    def test_setup_confirm_and_old_tokens(self):
        user = make_user(role='client')
        login = self._login(user)
        self.assertEqual(login.status_code, 200)                              # plain login
        old_access = login.cookies['access_token'].value
        self.c = _bearer(login)
        r = self.c.post(A + 'mfa/totp/setup/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['qr_png'].startswith('data:image/png;base64,'))
        self.assertIn(r.data['secret'], r.data['otpauth_uri'])
        bad = self.c.post(A + 'mfa/totp/confirm/', {'code': '000000'}, format='json')
        self.assertEqual(bad.status_code, 400)
        r = self.c.post(A + 'mfa/totp/confirm/', {'code': _code(user)}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(set(r.data['recovery_codes'])), 10)
        self.assertTrue(AccessToken(self.c.cookies['access_token'].value)['mfa'])
        self.assertTrue(User.objects.get(pk=user.pk).mfa_enabled)
        self.assertEqual(self.c.get(A + 'me/').status_code, 401)              # old token now refused
        self.c = _bearer(r)
        self.assertEqual(self.c.get(A + 'me/').status_code, 200)              # new token works
        stale = APIClient()
        stale.credentials(HTTP_AUTHORIZATION=f'Bearer {old_access}')
        self.assertEqual(stale.get(A + 'me/').status_code, 401)               # pre-enrolment token
        self.assertEqual(self.c.post(A + 'mfa/totp/setup/').status_code, 400)  # already enabled
        st = self.c.get(A + 'mfa/').data
        self.assertEqual((st['enabled'], st['required'], st['recovery_codes_remaining']), (True, False, 10))

    def test_secret_encrypted_at_rest(self):
        user = make_user(role='client')
        self._enrol(user)
        secret = MFADevice.objects.get(user=user).secret
        with connection.cursor() as cur:
            cur.execute('SELECT secret FROM user_mfa_devices WHERE user_id = %s', [user.pk])
            raw = cur.fetchone()[0]
        self.assertNotIn(secret, raw)


class LoginSecondStepTest(_Base):
    def setUp(self):
        super().setUp()
        self.user = make_user(role='client')
        self.recovery = self._enrol(self.user)

    def _challenge(self):
        r = self._login(self.user)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['mfa_required'])
        self.assertNotIn('access_token', r.cookies)
        return r.data['mfa_token']

    def _verify(self, token, code):
        return self.c.post(A + 'mfa/verify/', {'mfa_token': token, 'code': code}, format='json')

    def test_totp_replay_and_single_use_challenge(self):
        token = self._challenge()
        code = _code(self.user)
        r = self._verify(token, code)
        self.assertEqual((r.status_code, r.data['method']), (200, 'totp'))
        self.assertTrue(AccessToken(r.cookies['access_token'].value)['mfa'])
        self.assertTrue(RefreshToken(r.cookies['refresh_token'].value)['mfa'])
        self.assertEqual(self._verify(token, _code(self.user, 1)).status_code, 401)   # challenge used
        self.assertEqual(self._verify(self._challenge(), code).status_code, 400)       # code replayed
        self.assertEqual(self._verify('garbage', code).status_code, 401)

    def test_recovery_code_once_and_lockout(self):
        token = self._challenge()
        r = self._verify(token, self.recovery[0].upper())
        self.assertEqual((r.status_code, r.data['method'], r.data['recovery_codes_remaining']),
                         (200, 'recovery', 9))
        self.assertEqual(self._verify(self._challenge(), self.recovery[0]).status_code, 400)
        token = self._challenge()
        for _ in range(mfa.MAX_FAILURES - 1):          # the replayed recovery code was failure 1
            self.assertEqual(self._verify(token, '000000').status_code, 400)
        self.assertEqual(self._verify(token, _code(self.user)).status_code, 429)        # locked

    def test_otp_and_password_reset_paths_also_challenge(self):
        from apps.users.services import OTPService
        otp = OTPService.issue(self.user.phone, purpose='otp_login')
        r = self.c.post(A + 'otp/verify/', {'phone': self.user.phone, 'code': otp.code}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data.get('mfa_required'))
        self.assertNotIn('access_token', r.cookies)
        otp = OTPService.issue(self.user.phone, purpose='password_reset')
        r = self.c.post(A + 'password/reset/confirm/', {'phone': self.user.phone, 'code': otp.code,
                                                       'new_password': 'N3w-pass-word!'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data.get('mfa_required'))

    def test_refresh_without_claim_refused(self):
        refresh = RefreshToken.for_user(self.user)                              # no mfa claim
        self.c.cookies['refresh_token'] = str(refresh)
        self.assertEqual(self.c.post(A + 'token/refresh/').status_code, 401)
        refresh['mfa'] = True
        self.c.cookies['refresh_token'] = str(refresh)
        self.assertEqual(self.c.post(A + 'token/refresh/').status_code, 200)

    def test_disable_and_regenerate(self):
        c = _bearer(self._verify(self._challenge(), _code(self.user)))
        self.assertEqual(c.post(A + 'mfa/recovery-codes/', {'code': self.recovery[1]},
                                format='json').status_code, 400)                # TOTP only
        r = c.post(A + 'mfa/recovery-codes/', {'code': _code(self.user, 1)}, format='json')
        self.assertEqual((r.status_code, len(r.data['recovery_codes'])), (200, 10))
        MFADevice.objects.filter(user=self.user).update(last_used_step=0)
        self.assertEqual(c.post(A + 'mfa/totp/disable/', {'code': _code(self.user)},
                                format='json').status_code, 204)
        self.user.refresh_from_db()
        self.assertFalse(self.user.mfa_enabled)
        self.assertFalse(MFADevice.objects.filter(user=self.user).exists())
        self.assertTrue(AuditLog.objects.filter(target_id=str(self.user.pk),
                                                detail__startswith='MFA disabled').exists())


class PolicyTest(_Base):
    def test_required_roles(self):
        self.assertTrue(mfa.is_required(make_user(role='admin')))
        dev, _ = make_developer()                                               # org owner
        self.assertTrue(mfa.is_required(dev))
        self.assertFalse(mfa.is_required(make_user(role='client')))

    def test_not_enforced_logs_in_with_hint(self):
        admin = make_user(role='admin')
        r = self._login(admin)
        self.assertEqual(r.status_code, 200)
        self.assertIn('access_token', r.cookies)
        self.assertEqual(r['X-MFA-Enrollment-Required'], 'true')
        self.assertEqual(_bearer(r).get(A + 'me/').status_code, 200)

    @override_settings(MFA_ENFORCE=True)
    def test_enforced_enrolment_via_challenge(self):
        admin = make_user(role='admin')
        old = RefreshToken.for_user(admin)
        stale = APIClient()
        stale.credentials(HTTP_AUTHORIZATION=f'Bearer {old.access_token}')
        self.assertEqual(stale.get(A + 'me/').status_code, 401)                # pre-enforcement token
        r = self._login(admin)
        self.assertTrue(r.data['mfa_enrollment_required'])
        self.assertNotIn('access_token', r.cookies)
        token = r.data['mfa_token']
        self.assertEqual(self.c.post(A + 'mfa/verify/', {'mfa_token': token, 'code': '123456'},
                                     format='json').status_code, 401)           # wrong scope
        self.assertEqual(self.c.post(A + 'mfa/totp/setup/', {'mfa_token': token}, format='json').status_code, 200)
        r = self.c.post(A + 'mfa/totp/confirm/', {'mfa_token': token, 'code': _code(admin)}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(_bearer(r).get(A + 'me/').status_code, 200)
        self.assertEqual(APIClient().post(A + 'mfa/totp/setup/', {'mfa_token': token},
                                          format='json').status_code, 401)      # enrolment token used
        client = make_user(role='client')                                        # not required
        self.assertIn('access_token', self._login(client, APIClient()).cookies)

    def test_required_user_cannot_disable(self):
        admin = make_user(role='admin')
        self._enrol(admin)
        c = APIClient()
        c.force_authenticate(admin)
        self.assertEqual(c.post(A + 'mfa/totp/disable/', {'code': _code(admin)}, format='json').status_code, 403)


class AdminTest(_Base):
    def test_reset_action(self):
        from django.contrib.admin.sites import site
        staff = make_user(role='admin', is_staff=True)
        target = make_user(role='client')
        self._enrol(target)
        RefreshToken.for_user(target)
        from unittest.mock import patch
        with patch('django.contrib.messages.success'):
            site._registry[User].reset_mfa(MagicMock(user=staff), User.objects.filter(pk=target.pk))
        target.refresh_from_db()
        self.assertFalse(target.mfa_enabled)
        log = AuditLog.objects.get(target_id=str(target.pk))
        self.assertEqual((log.actor, log.detail), (staff, 'MFA reset by admin: admin action'))
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
        self.assertFalse(OutstandingToken.objects.filter(user=target, blacklistedtoken__isnull=True).exists())

    def test_admin_login_form(self):
        staff = make_user(role='admin', is_staff=True)
        page = self.client.get('/admin/login/')
        self.assertContains(page, 'name="otp_code"')
        data = {'username': staff.phone, 'password': 'pw'}
        self.assertEqual(self.client.post('/admin/login/', data).status_code, 302)   # not enrolled, not enforced
        self.client.logout()
        with override_settings(MFA_ENFORCE=True):
            self.assertContains(self.client.post('/admin/login/', data), 'Multi-factor authentication is required')
        self._enrol(staff)
        self.assertContains(self.client.post('/admin/login/', data), 'Enter the code')
        self.assertContains(self.client.post('/admin/login/', {**data, 'otp_code': '000000'}), 'Invalid')
        r = self.client.post('/admin/login/', {**data, 'otp_code': _code(staff)})
        self.assertEqual(r.status_code, 302)
