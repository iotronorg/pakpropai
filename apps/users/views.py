import logging
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from .serializers import SendOTPSerializer, VerifyOTPSerializer, UserSerializer, UserListSerializer
from .services import OTPService

logger = logging.getLogger(__name__)


class SendOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']

        try:
            otp = OTPService.issue(phone)
        except PermissionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        # Always log so dev/staging can verify without WhatsApp credentials.
        logger.warning(f"OTP for {phone}: {otp.code}")

        # Fire async delivery — non-blocking, retries up to 3× on failure.
        from apps.notifications.tasks import send_otp_async
        send_otp_async.delay(phone, otp.code)

        return Response(
            {'message': 'OTP sent to your WhatsApp.', 'phone': phone},
            status=status.HTTP_200_OK,
        )


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data['phone']
        code  = serializer.validated_data['code']

        try:
            user = OTPService.verify(phone, code)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        refresh = RefreshToken.for_user(user)
        return Response({
            'access':  str(refresh.access_token),
            'refresh': str(refresh),
            'user':    UserSerializer(user).data,
        })


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class UserListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != User.Role.ADMIN:
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        qs = User.objects.all().order_by('-created_at')
        serializer = UserListSerializer(qs, many=True)
        return Response({'count': qs.count(), 'results': serializer.data})

    def patch(self, request, pk):
        if request.user.role != User.Role.ADMIN:
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = UserListSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)