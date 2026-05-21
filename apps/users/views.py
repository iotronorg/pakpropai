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

from .models import User
from .serializers import SendOTPSerializer, VerifyOTPSerializer, UserSerializer, UserListSerializer, UserCreateSerializer
from .services import OTPService

logger = logging.getLogger(__name__)


class SendOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [OtpSendThrottle, OtpDailyThrottle]

    def post(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']

        try:
            otp = OTPService.issue(phone)
        except PermissionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        if settings.DEBUG:
            logger.warning("OTP for %s: %s", phone, otp.code)

        # Fire async delivery — non-blocking, retries up to 3× on failure.
        # Delivery errors (WhatsApp sandbox limits, network) must never crash this endpoint.
        # The OTP is already issued; the user can still receive it via the logged code in dev.
        try:
            from apps.notifications.tasks import send_otp_async
            send_otp_async.delay(phone, otp.code)
        except Exception:
            logger.warning("OTP WhatsApp delivery failed for %s — OTP was issued but not sent.", phone)

        return Response(
            {'message': 'OTP sent to your WhatsApp.', 'phone': phone},
            status=status.HTTP_200_OK,
        )


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [OtpSendThrottle, OtpDailyThrottle]

    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data['phone']
        code  = serializer.validated_data['code']

        try:
            user = OTPService.verify(phone, code)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if not user.is_active:
            return Response(
                {'error': 'Your account has been deactivated. Please contact support.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)
        response = Response({'user': UserSerializer(user).data})
        _set_auth_cookies(response, str(refresh.access_token), str(refresh), role=user.role)
        return response


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CookieTokenRefreshView(APIView):
    """POST /auth/token/refresh/ — issue a new access token from the httpOnly refresh cookie."""
    permission_classes = [AllowAny]

    def post(self, request):
        raw_refresh = request.COOKIES.get('refresh_token')
        if not raw_refresh:
            return Response({'error': 'No refresh token cookie.'}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            refresh = RefreshToken(raw_refresh)
            new_access = str(refresh.access_token)
        except TokenError as exc:
            response = Response({'error': str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
            _clear_auth_cookies(response)
            return response

        response = Response({'detail': 'Token refreshed.'})
        response.set_cookie(
            'access_token', new_access,
            max_age=_ACCESS_LIFETIME,
            httponly=True,
            samesite=_COOKIE_SAMESITE,
            secure=_COOKIE_SECURE,
            path='/',
        )
        return response


class LogoutView(APIView):
    """POST /auth/logout/ — blacklist the refresh token and clear auth cookies."""
    permission_classes = [IsAuthenticated]

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
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

    def get(self, request):
        err = self._require_admin(request)
        if err: return err
        qs = User.objects.all().order_by('-created_at')
        role = request.query_params.get('role')
        if role:
            qs = qs.filter(role=role)
        search = request.query_params.get('search')
        if search:
            qs = qs.filter(Q(phone__icontains=search) | Q(name__icontains=search))
        serializer = UserListSerializer(qs, many=True)
        return Response({'count': qs.count(), 'results': serializer.data})

    def post(self, request):
        err = self._require_admin(request)
        if err: return err
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserListSerializer(user).data, status=status.HTTP_201_CREATED)

    def patch(self, request, pk):
        err = self._require_admin(request)
        if err: return err
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = UserListSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        err = self._require_admin(request)
        if err: return err
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if str(user.pk) == str(request.user.pk):
            return Response({'error': 'Cannot delete your own account.'}, status=status.HTTP_400_BAD_REQUEST)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationPreferencesView(APIView):
    """GET/PATCH /auth/me/notification-preferences/ — per-user channel + event opt-outs."""
    permission_classes = [IsAuthenticated]

    def _get_prefs(self, user):
        from apps.notifications.models import UserNotificationPreference
        prefs, _ = UserNotificationPreference.objects.get_or_create(user=user)
        return prefs

    def get(self, request):
        from apps.notifications.serializers import NotificationPreferenceSerializer
        return Response(NotificationPreferenceSerializer(self._get_prefs(request.user)).data)

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

    def get(self, request):
        return Response({'csrfToken': get_csrf_token(request)})