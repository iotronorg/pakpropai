"""
Pending-action API (T-2012): the signed-in user lists, confirms or declines the
side-effect actions the assistant proposed to them.
"""
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers
from rest_framework.generics import ListAPIView, get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from . import confirmation
from .models import PendingAction


class ActionNotPending(exceptions.APIException):
    status_code = 409
    default_code = 'action_not_pending'


class PendingActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PendingAction
        fields = ['id', 'tool', 'side_effect', 'prompt', 'status', 'created_at', 'expires_at',
                  'confirmed_at', 'resolved_at']
        read_only_fields = fields


class ConfirmResultSerializer(serializers.Serializer):
    action = PendingActionSerializer()
    message = serializers.CharField()


def _own(request, pk) -> PendingAction:
    return get_object_or_404(PendingAction.objects.select_related('organization', 'user'),
                             pk=pk, user=request.user)


class PendingActionListView(ListAPIView):
    """The caller's open actions, newest first."""
    permission_classes = [IsAuthenticated]
    serializer_class = PendingActionSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return PendingAction.objects.none()
        return PendingAction.objects.filter(user=self.request.user, status=PendingAction.Status.PENDING,
                                            expires_at__gt=timezone.now())


class PendingActionConfirmView(APIView):
    """Run the stored action once. 409 if it expired, was resolved or was replaced."""
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: ConfirmResultSerializer,
                                            409: OpenApiResponse(description='Not pending any more')})
    def post(self, request, pk):
        action = _own(request, pk)
        from apps.whatsapp.models import WhatsAppSession
        if WhatsAppSession.objects.filter(
                user=request.user,
                conversation_mode=WhatsAppSession.ConversationMode.BLOCKED).exists():
            raise exceptions.PermissionDenied('Your account is suspended pending compliance review.')
        try:
            result = confirmation.confirm(action, 'api')
        except confirmation.NotConfirmable as exc:
            raise ActionNotPending(f'This action is {exc.status}.') from exc
        return Response({'action': PendingActionSerializer(action).data,
                         'message': confirmation.result_text(result)})


class PendingActionDeclineView(APIView):
    """Cancel an open action."""
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: PendingActionSerializer,
                                            409: OpenApiResponse(description='Not pending any more')})
    def post(self, request, pk):
        action = _own(request, pk)
        try:
            confirmation.decline(action)
        except confirmation.NotConfirmable as exc:
            raise ActionNotPending(f'This action is {exc.status}.') from exc
        return Response(PendingActionSerializer(action).data)
