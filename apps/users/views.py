import logging
from datetime import timedelta
from django.conf import settings
from django.db.models import Q
from django.middleware.csrf import get_token as get_csrf_token
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from apps.core.throttles import OtpSendThrottle, OtpDailyThrottle
from apps.core.permissions import IsAdminUser

_ACCESS_LIFETIME  = int(settings.SIMPLE_JWT.get('ACCESS_TOKEN_LIFETIME',  timedelta(minutes=15)).total_seconds())
_REFRESH_LIFETIME = int(settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME', timedelta(days=7)).total_seconds())
_COOKIE_SAMESITE  = 'Lax'
_COOKIE_SECURE    = not settings.DEBUG  # True in production


def _set_auth_cookies(response, access_token: str, refresh_token: str, role: str = ''):
    """Attach httpOnly auth cookies to a DRF Response."""
    response.set_cookie(
        'access_token', access_token,
        max_age=_ACCESS_LIFETIME,
        httponly=True,
        samesite=_COOKIE_SAMESITE,
        secure=_COOKIE_SECURE,
        path='/',
    )
    response.set_cookie(
        'refresh_token', refresh_token,
        max_age=_REFRESH_LIFETIME,
        httponly=True,
        samesite=_COOKIE_SAMESITE,
        secure=_COOKIE_SECURE,
        path='/',
    )
    # user_role is set server-side (httponly=True) so XSS cannot spoof role-based routing.
    # Next.js middleware reads this cookie for dashboard routing decisions.
    if role:
        response.set_cookie(
            'user_role', role,
            max_age=_REFRESH_LIFETIME,
            httponly=True,
            samesite=_COOKIE_SAMESITE,
            secure=_COOKIE_SECURE,
            path='/',
        )


def _clear_auth_cookies(response):
    """Delete auth cookies from the client."""
    response.delete_cookie('access_token',  path='/', samesite=_COOKIE_SAMESITE)
    response.delete_cookie('refresh_token', path='/', samesite=_COOKIE_SAMESITE)
    response.delete_cookie('user_role',     path='/', samesite=_COOKIE_SAMESITE)

from django.contrib.auth import get_user_model
from .models import User
from .serializers import (
    SendOTPSerializer, VerifyOTPSerializer, UserSerializer, UserListSerializer,
    UserCreateSerializer, PasswordLoginSerializer, PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer, PasswordChangeSerializer,
    RegistrationOTPVerifySerializer,
)
from . import openapi as API
from .services import OTPService
from apps.core.errors import ApiError

logger = logging.getLogger(__name__)


class SendOTPView(APIView):
    permission_classes = [AllowAny]
    ALLOWED_PURPOSES = ('otp_login', 'registration_verify')
    throttle_classes = [OtpSendThrottle, OtpDailyThrottle]

    @API.send_otp
    def post(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']

        # KI-030: other purposes (password_reset, org_registration) are issued only
        # by their own endpoints, with their own checks.
        purpose = request.data.get('purpose') or 'otp_login'
        if purpose not in self.ALLOWED_PURPOSES:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'purpose': f"Must be one of: {', '.join(self.ALLOWED_PURPOSES)}."})
        try:
            otp = OTPService.issue(phone, purpose=purpose)
        except ValueError as exc:
            raise ApiError(str(exc), 'auth.otp_rate_limited', 429)

        if settings.DEBUG:
            logger.warning("OTP for %s: %s", phone, otp.code)

        # Fire async delivery — non-blocking, retries up to 3× on failure.
        # Delivery errors (WhatsApp sandbox limits, network) must never crash this endpoint.
        # The OTP is already issued; the user can still receive it via the logged code in dev.
        try:
            from apps.notifications.delivery import enqueue_otp
            enqueue_otp(otp)  # outbox (ADR-014): the broker sees only the row id
        except Exception:
            logger.warning("OTP WhatsApp delivery failed for %s — OTP was issued but not sent.", phone)

        return Response(
            {'message': 'OTP sent to your WhatsApp.', 'phone': phone},
            status=status.HTTP_200_OK,
        )


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [OtpSendThrottle, OtpDailyThrottle]

    @API.verify_otp
    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data['phone']
        code  = serializer.validated_data['code']

        try:
            user = OTPService.verify_login(phone, code)
        except ValueError as exc:
            raise ApiError(str(exc), 'auth.otp_invalid', 400)

        if not user.is_active:
            raise ApiError('Your account has been deactivated. Please contact support.', 'auth.account_deactivated', 403)

        from .mfa import login_response              # T-1011: second factor when enrolled
        return login_response(user, {'user': UserSerializer(user).data})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @API.me
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    @API.me_update
    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CookieTokenRefreshView(APIView):
    """POST /auth/token/refresh/ — issue a new access token from the httpOnly refresh cookie.

    Mirrors simplejwt's TokenRefreshSerializer rotation logic: when
    ROTATE_REFRESH_TOKENS=True, the old refresh token is blacklisted and a new
    refresh token is issued alongside the new access token.
    """
    permission_classes = [AllowAny]

    @API.token_refresh
    def post(self, request):
        from rest_framework_simplejwt.settings import api_settings as jwt_settings

        raw_refresh = request.COOKIES.get('refresh_token')
        if not raw_refresh:
            raise ApiError('No refresh token cookie.', 'auth.refresh_missing', 401)
        try:
            refresh = RefreshToken(raw_refresh)
            new_access = str(refresh.access_token)
        except TokenError as exc:
            response = Response({'error': str(exc), 'code': 'auth.refresh_invalid'}, status=status.HTTP_401_UNAUTHORIZED)
            _clear_auth_cookies(response)
            return response
        # T-1011: a session that skipped the second factor cannot be renewed.
        from .mfa import token_acceptable
        owner = User.objects.filter(pk=refresh.get(jwt_settings.USER_ID_CLAIM), is_active=True).first()
        if owner is None or not token_acceptable(owner, refresh):
            response = Response({'error': 'Multi-factor authentication required. Log in again.', 'code': 'auth.mfa_required'},
                                status=status.HTTP_401_UNAUTHORIZED)
            _clear_auth_cookies(response)
            return response

        # Mirror simplejwt TokenRefreshSerializer rotation behaviour.
        # Without this, the old refresh token stays valid for its full 7-day
        # lifetime even though ROTATE_REFRESH_TOKENS=True — breaking the
        # sliding session security model.
        new_raw_refresh = raw_refresh
        if jwt_settings.ROTATE_REFRESH_TOKENS:
            if jwt_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass  # token_blacklist app not in INSTALLED_APPS — safe to skip
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            new_raw_refresh = str(refresh)

        response = Response({'detail': 'Token refreshed.'})
        response.set_cookie(
            'access_token', new_access,
            max_age=_ACCESS_LIFETIME,
            httponly=True,
            samesite=_COOKIE_SAMESITE,
            secure=_COOKIE_SECURE,
            path='/',
        )
        if jwt_settings.ROTATE_REFRESH_TOKENS:
            response.set_cookie(
                'refresh_token', new_raw_refresh,
                max_age=_REFRESH_LIFETIME,
                httponly=True,
                samesite=_COOKIE_SAMESITE,
                secure=_COOKIE_SECURE,
                path='/',
            )
        return response


class LogoutView(APIView):
    """POST /auth/logout/ — blacklist the refresh token and clear auth cookies."""
    permission_classes = [IsAuthenticated]

    @API.logout
    def post(self, request):
        raw_refresh = request.COOKIES.get('refresh_token') or request.data.get('refresh', '')
        response = Response({'detail': 'Logged out.'}, status=status.HTTP_200_OK)
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except TokenError:
                pass  # already expired — still clear cookies
        _clear_auth_cookies(response)
        return response


class UserListView(APIView):
    permission_classes = [IsAdminUser]

    def _require_admin(self, request):
        if request.user.role != User.Role.ADMIN:
            raise ApiError('Admin access required.', 'users.admin_required', 403)

    @API.users_list
    def get(self, request):
        self._require_admin(request)
        qs = User.objects.all().order_by('-created_at')
        role = request.query_params.get('role')
        if role:
            qs = qs.filter(role=role)
        search = request.query_params.get('search')
        if search:
            qs = qs.filter(Q(phone__icontains=search) | Q(name__icontains=search))
        serializer = UserListSerializer(qs, many=True)
        return Response({'count': qs.count(), 'results': serializer.data})

    @API.users_create
    def post(self, request):
        self._require_admin(request)
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserListSerializer(user).data, status=status.HTTP_201_CREATED)

    @API.user_update
    def patch(self, request, pk):
        self._require_admin(request)
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            raise ApiError('Not found.', 'users.not_found', 404)
        serializer = UserListSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @API.user_delete
    def delete(self, request, pk):
        self._require_admin(request)
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            raise ApiError('Not found.', 'users.not_found', 404)
        if str(user.pk) == str(request.user.pk):
            raise ApiError('Cannot delete your own account.', 'users.cannot_delete_self', 400)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserCollectionView(UserListView):
    """``/auth/users/``: list and create. ``PATCH``/``DELETE`` here had no ``pk`` and failed with a 500."""
    http_method_names = ['get', 'post', 'head', 'options']


class UserDetailView(UserListView):
    """``/auth/users/<pk>/``: update and delete one account."""
    http_method_names = ['patch', 'delete', 'options']


class NotificationPreferencesView(APIView):
    """GET/PATCH /auth/me/notification-preferences/ — per-user channel + event opt-outs."""
    permission_classes = [IsAuthenticated]

    def _get_prefs(self, user):
        from apps.notifications.models import UserNotificationPreference
        prefs, _ = UserNotificationPreference.objects.get_or_create(user=user)
        return prefs

    @API.preferences
    def get(self, request):
        from apps.notifications.serializers import NotificationPreferenceSerializer
        return Response(NotificationPreferenceSerializer(self._get_prefs(request.user)).data)

    @API.preferences_update
    def patch(self, request):
        from apps.notifications.serializers import NotificationPreferenceSerializer
        prefs = self._get_prefs(request.user)
        serializer = NotificationPreferenceSerializer(prefs, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CsrfTokenView(APIView):
    """
    GET /auth/csrf/
    Seeds the csrftoken cookie for browser clients.  Must be called once on
    app load before any state-mutating request.  No authentication required.
    """
    permission_classes = [AllowAny]
    authentication_classes = []  # no auth so CSRF middleware can set the cookie freely

    @API.csrf
    def get(self, request):
        return Response({'csrfToken': get_csrf_token(request)})


class PasswordLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes   = [OtpSendThrottle]

    @API.password_login
    def post(self, request):
        serializer = PasswordLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        identifier = serializer.validated_data['identifier']
        password   = serializer.validated_data['password']

        UserModel = get_user_model()
        user = (
            UserModel.objects.filter(phone=identifier).first()
            or UserModel.objects.filter(email=identifier).first()
        )

        if user is not None and not user.has_usable_password():
            return Response(
                {'detail': 'No password set. Use Forgot Password to set one.'},
                status=400,
            )

        if user is None or not user.check_password(password):
            return Response({'detail': 'Invalid credentials.'}, status=401)

        if not user.is_active:
            return Response({'detail': 'Account is inactive.'}, status=403)

        from .mfa import login_response              # T-1011: second factor when enrolled
        return login_response(user, UserSerializer(user).data)


class RegistrationOTPVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes   = [OtpSendThrottle]

    @API.registration_verify
    def post(self, request):
        serializer = RegistrationOTPVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        phone = serializer.validated_data['phone']
        code  = serializer.validated_data['code']

        try:
            OTPService.verify(phone, code, purpose='registration_verify')
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)

        UserModel = get_user_model()
        user = UserModel.objects.filter(phone=phone).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=400)
        user.is_phone_verified = True
        user.save(update_fields=['is_phone_verified'])
        return Response(
            {'detail': 'Phone verified. Your account is pending admin approval.'},
            status=200,
        )


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes   = [OtpSendThrottle, OtpDailyThrottle]

    @API.reset_request
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        phone     = serializer.validated_data['phone']
        UserModel = get_user_model()
        user      = UserModel.objects.filter(phone=phone).first()

        if user:
            try:
                otp = OTPService.issue(phone, purpose='password_reset')
                try:
                    from apps.notifications.delivery import enqueue_otp
                    enqueue_otp(otp)
                except Exception:
                    logger.warning("OTP delivery failed for %s during password reset.", phone)
            except ValueError:
                pass  # rate-limited — still return 200 (no enumeration)

        return Response(
            {'detail': 'If that phone is registered, a reset code was sent.'},
            status=200,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes   = [OtpSendThrottle]

    @API.reset_confirm
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        phone        = serializer.validated_data['phone']
        code         = serializer.validated_data['code']
        new_password = serializer.validated_data['new_password']

        try:
            OTPService.verify(phone, code, purpose='password_reset')
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)

        UserModel = get_user_model()
        user = UserModel.objects.filter(phone=phone).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=400)

        from django.utils import timezone
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
        for token in OutstandingToken.objects.filter(user=user, expires_at__gt=timezone.now()):
            BlacklistedToken.objects.get_or_create(token=token)

        user.set_password(new_password)
        user.save(update_fields=['password'])

        # T-1011: a reset proves phone access only; an enrolled user still needs the second factor.
        from .mfa import login_response
        return login_response(user, UserSerializer(user).data)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    @API.password_change
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        current_password = serializer.validated_data['current_password']
        new_password     = serializer.validated_data['new_password']

        if not request.user.check_password(current_password):
            return Response({'detail': 'Current password is incorrect.'}, status=400)

        request.user.set_password(new_password)
        request.user.save(update_fields=['password'])

        from django.utils import timezone
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
        for token in OutstandingToken.objects.filter(user=request.user, expires_at__gt=timezone.now()):
            BlacklistedToken.objects.get_or_create(token=token)

        return Response({'detail': 'Password updated.'}, status=200)