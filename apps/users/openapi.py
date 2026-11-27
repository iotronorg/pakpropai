"""
OpenAPI descriptions of the auth and user endpoints (T-1091, KI-037).

The views are plain ``APIView``s; these describe their real answers and are
used only for the schema. Keep them in step with ``views.py`` and ``mfa.py``.
"""
from drf_spectacular.utils import (
    OpenApiParameter, OpenApiResponse, PolymorphicProxySerializer, extend_schema, inline_serializer,
)
from rest_framework import serializers

from .serializers import (
    PasswordChangeSerializer, PasswordLoginSerializer, PasswordResetConfirmSerializer, PasswordResetRequestSerializer,
    RegistrationOTPVerifySerializer, SendOTPSerializer, UserCreateSerializer, UserListSerializer, UserSerializer,
    VerifyOTPSerializer,
)

ERROR = OpenApiResponse(description='Refused; `error`/`detail` and `error_info` (ADR-017)')


def message(name: str):
    return inline_serializer(name, {'detail': serializers.CharField()})


# ``mfa.login_response``: tokens are set as httpOnly cookies, or a second step is asked for.
MFA_CHALLENGE = inline_serializer('MfaChallenge', {
    'mfa_required': serializers.BooleanField(), 'mfa_token': serializers.CharField(),
    'methods': serializers.ListField(child=serializers.CharField())})
MFA_ENROLMENT = inline_serializer('MfaEnrollmentRequired', {
    'mfa_enrollment_required': serializers.BooleanField(), 'mfa_token': serializers.CharField()})
LOGIN_NOTE = ('Signed in: auth cookies are set (header `X-MFA-Enrollment-Required: true` when the account should '
              'enrol). Enrolled accounts get `MfaChallenge` instead; finish at `/auth/mfa/verify/`. With '
              '`MFA_ENFORCE`, accounts that must enrol get `MfaEnrollmentRequired`.')


def login(name: str, signed_in):
    return PolymorphicProxySerializer(component_name=name, serializers=[signed_in, MFA_CHALLENGE, MFA_ENROLMENT],
                                      resource_type_field_name=None)


send_otp = extend_schema(
    request=SendOTPSerializer,
    responses={200: inline_serializer('OtpSent', {'message': serializers.CharField(), 'phone': serializers.CharField()}),
               400: ERROR, 429: ERROR})
verify_otp = extend_schema(
    request=VerifyOTPSerializer, description=LOGIN_NOTE,
    responses={200: login('OtpLoginResult', inline_serializer('OtpLoginUser', {'user': UserSerializer()})),
               400: ERROR, 403: ERROR})
password_login = extend_schema(
    request=PasswordLoginSerializer, description=LOGIN_NOTE,
    responses={200: login('PasswordLoginResult', UserSerializer), 400: ERROR, 401: ERROR, 403: ERROR})
token_refresh = extend_schema(
    request=None, description='Reads and rotates the httpOnly `refresh_token` cookie; sets a new `access_token`.',
    responses={200: message('TokenRefreshed'), 401: ERROR})
logout = extend_schema(
    request=inline_serializer('LogoutRequest', {'refresh': serializers.CharField(required=False,
                                                help_text='Only when the refresh token is not in a cookie')}),
    responses={200: message('LoggedOut')})
me = extend_schema(responses=UserSerializer)
me_update = extend_schema(request=UserSerializer, responses={200: UserSerializer, 400: ERROR})
csrf = extend_schema(responses={200: inline_serializer('CsrfToken', {'csrfToken': serializers.CharField()})})
registration_verify = extend_schema(request=RegistrationOTPVerifySerializer,
                                    responses={200: message('RegistrationVerified'), 400: ERROR})
reset_request = extend_schema(request=PasswordResetRequestSerializer,
                              responses={200: message('PasswordResetRequested')},
                              description='Always 200, whether or not the phone is registered.')
reset_confirm = extend_schema(request=PasswordResetConfirmSerializer, description=LOGIN_NOTE,
                              responses={200: login('PasswordResetResult', UserSerializer), 400: ERROR})
password_change = extend_schema(request=PasswordChangeSerializer,
                                responses={200: message('PasswordChanged'), 400: ERROR})

users_list = extend_schema(
    operation_id='auth_users_list',
    parameters=[OpenApiParameter('role', str), OpenApiParameter('search', str, description='Phone or name')],
    responses={200: inline_serializer('UserPage', {'count': serializers.IntegerField(),
                                                   'results': UserListSerializer(many=True)}), 403: ERROR})
users_create = extend_schema(request=UserCreateSerializer, responses={201: UserListSerializer, 400: ERROR, 403: ERROR})
user_update = extend_schema(request=UserListSerializer,
                            responses={200: UserListSerializer, 400: ERROR, 403: ERROR, 404: ERROR})
user_delete = extend_schema(responses={204: None, 400: ERROR, 403: ERROR, 404: ERROR})


def _preferences():
    from apps.notifications.serializers import NotificationPreferenceSerializer
    return NotificationPreferenceSerializer


preferences = extend_schema(responses=_preferences())
preferences_update = extend_schema(request=_preferences(), responses={200: _preferences(), 400: ERROR})
