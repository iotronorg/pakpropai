"""
Multi-factor authentication: TOTP + recovery codes (T-1011, ADR-026).

Who: any user may enrol; it is **required** for platform staff (role admin,
is_staff, is_superuser) and organisation owners / org admins. With
``MFA_ENFORCE`` off (the default, for rollout) required users who have not
enrolled still log in, and the response carries ``X-MFA-Enrollment-Required``.
With it on they must enrol before any token is issued.

Login (every path that issues tokens calls :func:`login_response`):

* not enrolled, not enforced → tokens as before;
* enrolled → ``{"mfa_required": true, "mfa_token": …}`` and **no** tokens;
  ``POST /auth/mfa/verify/`` with a TOTP or recovery code issues them;
* required, not enrolled, enforced → ``{"mfa_enrollment_required": true,
  "mfa_token": …}``; the token only allows setup + confirm, which then logs in.

Tokens issued after the second factor carry the claim ``mfa: true``. The JWT
authentication class rejects a token without it for an enrolled user (and,
when enforced, for a required user), so tokens from before enrolment, or
from a login that skipped the second step, stop working.

TOTP is RFC 6238 (SHA-1, 30 s, 6 digits, ±1 step) in the standard library;
each step is accepted once. Five failed codes lock the second step for 15
minutes. Challenge tokens are signed, expire after 5 minutes and are single-use.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

STEP = 30
DIGITS = 6
WINDOW = 1
RECOVERY_CODES = 10
_RECOVERY_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'     # no 0/o/1/l/i
CHALLENGE_SALT = 'users.mfa.challenge.v1'
CHALLENGE_MAX_AGE = 300
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
SCOPE_VERIFY = 'verify'
SCOPE_ENROLL = 'enroll'


class MFAError(Exception):
    http_status = 400


class MFALocked(MFAError):
    http_status = 429


class ChallengeInvalid(MFAError):
    http_status = 401


# ── TOTP (RFC 6238) ───────────────────────────────────────────────────────────

def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + '=' * (-len(secret) % 8), casefold=True)


def totp_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP)


def match_step(secret: str, code: str, *, now: float | None = None) -> int | None:
    """The step ``code`` belongs to (within ±WINDOW), or None."""
    code = (code or '').strip().replace(' ', '')
    if len(code) != DIGITS or not code.isdigit():
        return None
    base = current_step(now)
    for step in range(base - WINDOW, base + WINDOW + 1):
        if hmac.compare_digest(totp_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str) -> str:
    issuer = settings.MFA_ISSUER
    return (f'otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}'
            f'&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}')


def qr_data_uri(uri: str) -> str:
    import io

    import qrcode
    buf = io.BytesIO()
    qrcode.make(uri, box_size=6, border=2).save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


# ── Policy ────────────────────────────────────────────────────────────────────

def is_required(user) -> bool:
    """Platform staff and org owners / org admins must use MFA."""
    if getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser:
        return True
    from apps.organizations.models import OrganizationMembership
    return OrganizationMembership.objects.filter(
        user=user, is_active=True, role__in=('owner', 'org_admin')).exists()


def is_required_cached(user) -> bool:
    key = f'mfa:required:{user.pk}'
    hit = cache.get(key)
    if hit is None:
        hit = is_required(user)
        cache.set(key, hit, 60)
    return hit


def token_acceptable(user, token) -> bool:
    """Used by the JWT authentication class on every request."""
    if token.get('mfa') is True:
        return True
    if user.mfa_enabled:
        return False
    return not (settings.MFA_ENFORCE and is_required_cached(user))


# ── Failure lockout ───────────────────────────────────────────────────────────

def _fail_key(user) -> str:
    return f'mfa:fail:{user.pk}'


def check_not_locked(user):
    if int(cache.get(_fail_key(user)) or 0) >= MAX_FAILURES:
        raise MFALocked('Too many wrong codes. Try again in 15 minutes.')


def _record_failure(user):
    key = _fail_key(user)
    cache.add(key, 0, LOCKOUT_SECONDS)
    cache.incr(key)


def _clear_failures(user):
    cache.delete(_fail_key(user))


# ── Codes ─────────────────────────────────────────────────────────────────────

def _recovery_hash(user, code: str) -> str:
    from apps.core.crypto import blind_index
    norm = (code or '').strip().lower().replace('-', '').replace(' ', '')
    return blind_index(f'mfa-recovery:v1:{user.pk}:{norm}')


def new_recovery_codes(user) -> list[str]:
    """Replace the user's recovery codes; returns the plaintext once."""
    from .models import MFARecoveryCode
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = ''.join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f'{raw[:5]}-{raw[5:]}')
    with transaction.atomic():
        MFARecoveryCode.objects.filter(user=user).delete()
        MFARecoveryCode.objects.bulk_create(
            [MFARecoveryCode(user=user, code_hash=_recovery_hash(user, c)) for c in codes])
    return codes


def recovery_codes_left(user) -> int:
    return user.mfa_recovery_codes.filter(used_at__isnull=True).count()


def verify_code(user, code: str, *, allow_recovery: bool = True) -> str:
    """
    Check a TOTP or (if allowed) recovery code for an enrolled user. Returns
    'totp' or 'recovery'. Raises MFAError / MFALocked. Counts failures.
    """
    from .models import MFADevice, MFARecoveryCode
    check_not_locked(user)
    device = MFADevice.objects.filter(user=user, confirmed_at__isnull=False).first()
    if device is None:
        raise MFAError('MFA is not set up for this account.')
    step = match_step(device.secret, code)
    if step is not None and MFADevice.objects.filter(
            pk=device.pk, last_used_step__lt=step).update(last_used_step=step):
        _clear_failures(user)
        return 'totp'
    if allow_recovery and step is None:
        used = MFARecoveryCode.objects.filter(
            user=user, code_hash=_recovery_hash(user, code), used_at__isnull=True,
        ).update(used_at=timezone.now())
        if used:
            _clear_failures(user)
            return 'recovery'
    _record_failure(user)
    raise MFAError('Invalid or already used code.')


# ── Enrolment ─────────────────────────────────────────────────────────────────

def start_setup(user) -> dict:
    """Create (or replace) an unconfirmed device. 409-style error if already enabled."""
    from .models import MFADevice
    if user.mfa_enabled:
        raise MFAError('MFA is already enabled; disable it first to re-enrol.')
    secret = new_secret()
    MFADevice.objects.update_or_create(user=user, defaults={
        'secret': secret, 'confirmed_at': None, 'last_used_step': 0})
    uri = otpauth_uri(secret, user.email or user.phone)
    return {'secret': secret, 'otpauth_uri': uri, 'qr_png': qr_data_uri(uri)}


def confirm_setup(user, code: str) -> list[str]:
    """Confirm the pending device with a first code; returns new recovery codes."""
    from .models import MFADevice
    check_not_locked(user)
    device = MFADevice.objects.filter(user=user, confirmed_at__isnull=True).first()
    if device is None:
        raise MFAError('Start MFA setup first.')
    step = match_step(device.secret, code)
    if step is None:
        _record_failure(user)
        raise MFAError('Invalid code. Check the time on your device and try again.')
    with transaction.atomic():
        device.confirmed_at = timezone.now()
        device.last_used_step = step
        device.save(update_fields=['confirmed_at', 'last_used_step'])
        type(user).objects.filter(pk=user.pk).update(mfa_enabled=True)
        user.mfa_enabled = True
        codes = new_recovery_codes(user)
    _clear_failures(user)
    return codes


def disable(user, *, by=None, reason: str = '') -> None:
    """Remove the device and codes, end sessions. ``by`` = admin performing a reset."""
    from apps.core.models import AuditLog
    from .models import MFADevice, MFARecoveryCode
    with transaction.atomic():
        MFADevice.objects.filter(user=user).delete()
        MFARecoveryCode.objects.filter(user=user).delete()
        type(user).objects.filter(pk=user.pk).update(mfa_enabled=False)
        user.mfa_enabled = False
        AuditLog.log(actor=by or user, action=AuditLog.Action.UPDATE, target=user,
                     detail=('MFA reset by admin' if by else 'MFA disabled by user')
                            + (f': {reason[:200]}' if reason else ''))
    revoke_sessions(user)
    _clear_failures(user)


def revoke_sessions(user) -> None:
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
    for token in OutstandingToken.objects.filter(user=user, expires_at__gt=timezone.now()):
        BlacklistedToken.objects.get_or_create(token=token)


# ── Challenge tokens ──────────────────────────────────────────────────────────

def issue_challenge(user, scope: str) -> str:
    return signing.dumps({'u': str(user.pk), 's': scope, 'n': secrets.token_urlsafe(12)},
                         salt=CHALLENGE_SALT)


def read_challenge(token: str, scopes: tuple[str, ...], *, consume: bool):
    """User for a valid challenge of one of ``scopes``; ``consume`` makes it single-use."""
    from .models import User
    try:
        data = signing.loads(token or '', salt=CHALLENGE_SALT, max_age=CHALLENGE_MAX_AGE)
    except signing.BadSignature:
        raise ChallengeInvalid('MFA session expired. Log in again.') from None
    if data.get('s') not in scopes or cache.get(f"mfa:used:{data.get('n')}"):
        raise ChallengeInvalid('MFA session expired. Log in again.')
    user = User.objects.filter(pk=data.get('u'), is_active=True).first()
    if user is None:
        raise ChallengeInvalid('MFA session expired. Log in again.')
    if consume:
        cache.set(f"mfa:used:{data['n']}", 1, CHALLENGE_MAX_AGE + 60)
    return user


# ── Login gate ────────────────────────────────────────────────────────────────

def login_response(user, body, *, status: int = 200):
    """
    The single place that issues login tokens. ``body`` is the normal success
    payload (dict) — used only when no second step is needed.
    """
    from rest_framework.response import Response
    if user.mfa_enabled:
        return Response({'mfa_required': True, 'mfa_token': issue_challenge(user, SCOPE_VERIFY),
                         'methods': ['totp', 'recovery_code']}, status=200)
    required = is_required(user)
    if required and settings.MFA_ENFORCE:
        return Response({'mfa_enrollment_required': True,
                         'mfa_token': issue_challenge(user, SCOPE_ENROLL)}, status=200)
    response = Response(body, status=status)
    issue_tokens(response, user, mfa=False)
    if required:
        response['X-MFA-Enrollment-Required'] = 'true'
    return response


def issue_tokens(response, user, *, mfa: bool):
    from rest_framework_simplejwt.tokens import RefreshToken
    from .views import _set_auth_cookies
    refresh = RefreshToken.for_user(user)
    if mfa:
        refresh['mfa'] = True
    _set_auth_cookies(response, str(refresh.access_token), str(refresh), role=user.role)
    return response
