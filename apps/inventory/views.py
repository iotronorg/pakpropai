import logging

from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminUser, IsOrgAdmin
from apps.inventory.models import (
    ExternalPlatformConnection,
    SyncConflictAlert,
    WebhookDeliveryRecord,
)

logger = logging.getLogger(__name__)

_SECRET_MASK = '••••••'


# ── Serializers ────────────────────────────────────────────────────────────────

class ExternalPlatformConnectionSerializer(serializers.ModelSerializer):

    class Meta:
        model  = ExternalPlatformConnection
        fields = [
            'id', 'platform', 'api_key', 'api_secret', 'base_url',
            'is_active', 'sync_direction', 'conflict_resolution',
            'last_synced_at', 'sync_status', 'error_detail', 'field_mappings', 'created_at',
        ]
        read_only_fields = ['id', 'last_synced_at', 'sync_status', 'created_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.api_key:
            data['api_key'] = _SECRET_MASK
        if instance.api_secret:
            data['api_secret'] = _SECRET_MASK
        return data

    def update(self, instance, validated_data):
        if validated_data.get('api_key') == _SECRET_MASK:
            validated_data.pop('api_key')
        if validated_data.get('api_secret') == _SECRET_MASK:
            validated_data.pop('api_secret')
        return super().update(instance, validated_data)


class SyncConflictAlertSerializer(serializers.ModelSerializer):

    class Meta:
        model  = SyncConflictAlert
        fields = [
            'id', 'org', 'connection', 'property', 'external_delta', 'internal_state',
            'resolution', 'created_at', 'resolved_at', 'resolved_by',
        ]
        read_only_fields = ['id', 'org', 'connection', 'property', 'external_delta',
                            'internal_state', 'created_at']


class WebhookDeliveryRecordSerializer(serializers.ModelSerializer):

    class Meta:
        model  = WebhookDeliveryRecord
        fields = [
            'id', 'event_type', 'payload', 'status',
            'attempt_count', 'delivered_at', 'error_detail', 'created_at',
        ]
        read_only_fields = fields


# ── Views ──────────────────────────────────────────────────────────────────────

class _IsOrgAdminOrAdmin(IsOrgAdmin):
    def has_permission(self, request, view):
        if IsAdminUser().has_permission(request, view):
            return True
        return super().has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        if IsAdminUser().has_permission(request, view):
            return True
        try:
            user_org = request.user.owned_organization
        except Exception:
            return False
        obj_org = getattr(obj, 'org', None) or getattr(obj, 'org_id', None)
        if obj_org is None:
            return False
        org_id = obj_org.id if hasattr(obj_org, 'id') else obj_org
        return str(org_id) == str(user_org.id)


def _get_org(user):
    if hasattr(user, 'owned_organization'):
        return user.owned_organization
    return None


class ExternalPlatformConnectionViewSet(viewsets.ModelViewSet):
    serializer_class   = ExternalPlatformConnectionSerializer
    permission_classes = [IsAuthenticated, _IsOrgAdminOrAdmin]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'admin':
            return ExternalPlatformConnection.objects.select_related('org').all()
        org = _get_org(user)
        if not org:
            return ExternalPlatformConnection.objects.none()
        return ExternalPlatformConnection.objects.filter(org=org)

    def perform_create(self, serializer):
        org = _get_org(self.request.user)
        serializer.save(org=org)

    @action(detail=True, methods=['post'], url_path='test')
    def test_connection(self, request, pk=None):
        connection = self.get_object()
        from apps.inventory.sync_engine import _adapter_for
        from apps.inventory.adapters.base import AdapterConfig
        try:
            adapter = _adapter_for(connection)
            listings = adapter.poll_listings(limit=1)
            return Response({'status': 'connected', 'detail': f'Got {len(listings)} listing(s)'})
        except Exception as exc:
            return Response({'status': 'error', 'detail': str(exc)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='sync')
    def trigger_sync(self, request, pk=None):
        connection = self.get_object()
        from apps.inventory.tasks import sync_external_platform
        connection.sync_status = 'syncing'
        connection.save(update_fields=['sync_status'])
        sync_external_platform.delay(str(connection.id))
        return Response({'detail': 'Sync queued.'}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        connection = self.get_object()
        records = WebhookDeliveryRecord.objects.filter(connection=connection).order_by('-created_at')[:100]
        serializer = WebhookDeliveryRecordSerializer(records, many=True)
        return Response(serializer.data)


class SyncConflictAlertViewSet(viewsets.GenericViewSet,
                                viewsets.mixins.ListModelMixin,
                                viewsets.mixins.RetrieveModelMixin):
    serializer_class   = SyncConflictAlertSerializer
    permission_classes = [IsAuthenticated, _IsOrgAdminOrAdmin]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'admin':
            return SyncConflictAlert.objects.select_related('org', 'property', 'connection').all()
        org = _get_org(user)
        if not org:
            return SyncConflictAlert.objects.none()
        return SyncConflictAlert.objects.filter(org=org).select_related('property', 'connection')

    @action(detail=True, methods=['patch'], url_path='resolve')
    def resolve(self, request, pk=None):
        alert = self.get_object()
        resolution = request.data.get('resolution')
        if resolution not in ('internal_wins', 'external_wins', 'manual'):
            return Response({'detail': 'Invalid resolution.'}, status=status.HTTP_400_BAD_REQUEST)
        alert.resolution  = resolution
        alert.resolved_at = timezone.now()
        alert.resolved_by = request.user
        alert.save(update_fields=['resolution', 'resolved_at', 'resolved_by'])
        return Response(SyncConflictAlertSerializer(alert).data)
