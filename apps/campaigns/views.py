from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.core.permissions import get_user_org

from .models import Campaign
from .serializers import CampaignSerializer


class CampaignViewSet(ModelViewSet):
    """
    Org-scoped campaign management.
    Only developer role (org admin) can access.
    """
    serializer_class   = CampaignSerializer
    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        if user.role != 'developer':
            return Campaign.objects.none()
        org = get_user_org(user)
        if not org:
            return Campaign.objects.none()
        qs = Campaign.objects.filter(organization=org).select_related('created_by')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if user.role != 'developer':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only organization admins can create campaigns.")
        org = get_user_org(user)
        if not org:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("No organization found for your account.")
        serializer.save(organization=org, created_by=user, status=Campaign.Status.DRAFT)

    def perform_update(self, serializer):
        campaign = self.get_object()
        if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.SCHEDULED):
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Only draft or scheduled campaigns can be edited.")
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        campaign = self.get_object()
        if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.CANCELLED):
            return Response(
                {"detail": "Only draft or cancelled campaigns can be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='send')
    def send(self, request, pk=None):
        """POST /campaigns/{id}/send/ — queue the campaign for immediate delivery."""
        campaign = self.get_object()
        if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.SCHEDULED):
            return Response(
                {"detail": f"Cannot send a campaign with status '{campaign.status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.status = Campaign.Status.SENDING
        campaign.scheduled_at = None
        campaign.save(update_fields=['status', 'scheduled_at'])

        from .tasks import send_campaign_messages
        send_campaign_messages.delay(str(campaign.id))

        return Response({"detail": "Campaign queued for delivery.", "id": str(campaign.id)})

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """POST /campaigns/{id}/cancel/ — cancel a scheduled campaign."""
        campaign = self.get_object()
        if campaign.status != Campaign.Status.SCHEDULED:
            return Response(
                {"detail": "Only scheduled campaigns can be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.status = Campaign.Status.CANCELLED
        campaign.save(update_fields=['status'])
        return Response({"detail": "Campaign cancelled.", "id": str(campaign.id)})

    @action(detail=True, methods=['post'], url_path='schedule')
    def schedule(self, request, pk=None):
        """POST /campaigns/{id}/schedule/ — set scheduled_at and mark as scheduled."""
        campaign = self.get_object()
        if campaign.status != Campaign.Status.DRAFT:
            return Response(
                {"detail": "Only draft campaigns can be scheduled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        scheduled_at = request.data.get('scheduled_at')
        if not scheduled_at:
            return Response(
                {"detail": "scheduled_at is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from django.utils.dateparse import parse_datetime
        from django.utils import timezone
        dt = parse_datetime(scheduled_at)
        if not dt:
            return Response(
                {"detail": "Invalid scheduled_at format. Use ISO 8601."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if dt <= timezone.now():
            return Response(
                {"detail": "scheduled_at must be in the future."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.scheduled_at = dt
        campaign.status = Campaign.Status.SCHEDULED
        campaign.save(update_fields=['scheduled_at', 'status'])
        return Response(CampaignSerializer(campaign).data)
