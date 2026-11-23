"""MFA endpoints under /api/v1/auth/mfa/ (T-1011, ADR-026). See apps/users/mfa.py."""
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from . import mfa
from .serializers import UserSerializer


class MfaThrottle(AnonRateThrottle):
    scope = 'mfa_verify'


class _Code(serializers.Serializer):
    code = serializers.CharField(max_length=20)


class _ChallengeCode(serializers.Serializer):
    mfa_token = serializers.CharField(max_length=500)
    code      = serializers.CharField(max_length=20)


class _OptionalChallenge(serializers.Serializer):
    mfa_token = serializers.CharField(max_length=500, required=False)
    code      = serializers.CharField(max_length=20, required=False)


def _error(exc):
    return Response({'detail': str(exc)}, status=exc.http_status)


def _enrolling_user(request, *, consume: bool):
    """Authenticated user, or the user of an enrolment challenge (scope enroll)."""
    if request.user and request.user.is_authenticated:
        return request.user, False
    token = request.data.get('mfa_token', '')
    return mfa.read_challenge(token, (mfa.SCOPE_ENROLL,), consume=consume), True


# Signed in (JWT), or anonymous with an enrolment mfa_token in the body.
_ENROL_AUTH = [{'jwtBearer': []}, {'jwtCookie': []}, {}]

_STATUS = inline_serializer('MfaStatus', {
    'enabled': serializers.BooleanField(), 'required': serializers.BooleanField(),
    'enforced': serializers.BooleanField(), 'recovery_codes_remaining': serializers.IntegerField()})
_SETUP = inline_serializer('MfaSetup', {
    'secret': serializers.CharField(), 'otpauth_uri': serializers.CharField(),
    'qr_png': serializers.CharField()})
_CODES = inline_serializer('MfaRecoveryCodes', {
    'recovery_codes': serializers.ListField(child=serializers.CharField())})


class MfaStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=_STATUS)
    def get(self, request):
        from django.conf import settings
        u = request.user
        return Response({'enabled': u.mfa_enabled, 'required': mfa.is_required(u),
                         'enforced': settings.MFA_ENFORCE,
                         'recovery_codes_remaining': mfa.recovery_codes_left(u) if u.mfa_enabled else 0})


class MfaVerifyView(APIView):
    """POST {mfa_token, code}: second login step → auth cookies + user."""
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [MfaThrottle]

    @extend_schema(request=_ChallengeCode, responses=UserSerializer)
    def post(self, request):
        body = _ChallengeCode(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            user = mfa.read_challenge(body.validated_data['mfa_token'], (mfa.SCOPE_VERIFY,), consume=False)
            method = mfa.verify_code(user, body.validated_data['code'])
            mfa.read_challenge(body.validated_data['mfa_token'], (mfa.SCOPE_VERIFY,), consume=True)
        except mfa.MFAError as exc:
            return _error(exc)
        response = Response({'user': UserSerializer(user).data, 'method': method,
                             'recovery_codes_remaining': mfa.recovery_codes_left(user)})
        return mfa.issue_tokens(response, user, mfa=True)


def _lenient():
    from .authentication import MfaLenientAuthentication
    return [MfaLenientAuthentication]


class TotpSetupView(APIView):
    """POST: new TOTP secret + otpauth URI + QR (authenticated, or with an enrolment mfa_token)."""
    permission_classes = [AllowAny]
    authentication_classes = _lenient()
    throttle_classes = [MfaThrottle]

    @extend_schema(request=_OptionalChallenge, responses=_SETUP, auth=_ENROL_AUTH)
    def post(self, request):
        try:
            user, _ = _enrolling_user(request, consume=False)
            return Response(mfa.start_setup(user))
        except mfa.MFAError as exc:
            return _error(exc)


class TotpConfirmView(APIView):
    """POST {code[, mfa_token]}: confirm the device; returns recovery codes once and logs in with MFA."""
    permission_classes = [AllowAny]
    authentication_classes = _lenient()
    throttle_classes = [MfaThrottle]

    @extend_schema(request=_OptionalChallenge, responses=_CODES, auth=_ENROL_AUTH)
    def post(self, request):
        code = str(request.data.get('code', ''))
        try:
            user, via_challenge = _enrolling_user(request, consume=False)
            codes = mfa.confirm_setup(user, code)
            if via_challenge:
                mfa.read_challenge(request.data.get('mfa_token', ''), (mfa.SCOPE_ENROLL,), consume=True)
        except mfa.MFAError as exc:
            return _error(exc)
        mfa.revoke_sessions(user)          # tokens from before enrolment lack the mfa claim
        response = Response({'recovery_codes': codes, 'user': UserSerializer(user).data})
        return mfa.issue_tokens(response, user, mfa=True)


class TotpDisableView(APIView):
    """POST {code}: turn MFA off (not allowed where it is required; ask an admin to reset)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(request=_Code, responses={204: None})
    def post(self, request):
        body = _Code(data=request.data)
        body.is_valid(raise_exception=True)
        user = request.user
        if mfa.is_required(user):
            return Response({'detail': 'MFA is required for your role. An administrator can reset it.'},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            mfa.verify_code(user, body.validated_data['code'])
        except mfa.MFAError as exc:
            return _error(exc)
        mfa.disable(user)
        from .views import _clear_auth_cookies
        response = Response(status=status.HTTP_204_NO_CONTENT)
        _clear_auth_cookies(response)
        return response


class RecoveryCodesView(APIView):
    """POST {code}: replace recovery codes (needs a current TOTP code, not a recovery code)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(request=_Code, responses=_CODES)
    def post(self, request):
        body = _Code(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            mfa.verify_code(request.user, body.validated_data['code'], allow_recovery=False)
        except mfa.MFAError as exc:
            return _error(exc)
        return Response({'recovery_codes': mfa.new_recovery_codes(request.user)})
