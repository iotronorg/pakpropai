import logging

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsOrgAdmin

logger = logging.getLogger(__name__)


class ProvisioningRecordSerializer(serializers.Serializer):
    operational_mode       = serializers.CharField()
    last_step              = serializers.CharField(allow_blank=True)
    error_detail           = serializers.CharField(allow_blank=True)
    waba_verified_at       = serializers.DateTimeField(allow_null=True)
    webhook_verified_at    = serializers.DateTimeField(allow_null=True)
    templates_approved_at  = serializers.DateTimeField(allow_null=True)
    data_migrated_at       = serializers.DateTimeField(allow_null=True)
    sandbox_leads_migrated      = serializers.IntegerField()
    sandbox_sessions_migrated   = serializers.IntegerField()
    sandbox_properties_migrated = serializers.IntegerField()
    updated_at             = serializers.DateTimeField()


class ProvisioningStatusView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get(self, request):
        from apps.organizations.models import OrgProvisioningRecord
        try:
            org = request.user.owned_organization
        except Exception:
            return Response({'detail': 'No organization found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            record = org.provisioning_record
        except OrgProvisioningRecord.DoesNotExist:
            return Response({'detail': 'Provisioning record not found.'}, status=status.HTTP_404_NOT_FOUND)

        return Response(ProvisioningRecordSerializer(record).data)


class ProvisioningStartView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def post(self, request):
        from apps.organizations.models import OrgProvisioningRecord
        from apps.whatsapp.tasks import provision_organization_live

        try:
            org = request.user.owned_organization
        except Exception:
            return Response({'detail': 'No organization found.'}, status=status.HTTP_404_NOT_FOUND)

        if org.operational_mode == 'production':
            return Response(
                {'detail': 'Organization is already in production.'},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            wa_config = org.whatsapp_config
        except Exception:
            return Response(
                {'detail': 'WhatsApp config not found. Configure waba_id and access_token first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not wa_config.waba_id:
            return Response(
                {'detail': 'waba_id must be set before provisioning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not wa_config.access_token:
            return Response(
                {'detail': 'access_token must be set before provisioning.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        OrgProvisioningRecord.get_or_create_for_org(org)
        provision_organization_live.delay(str(org.id))

        return Response({'detail': 'Provisioning started.'}, status=status.HTTP_202_ACCEPTED)


class ProvisioningRetryView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def post(self, request):
        from apps.organizations.models import OrgProvisioningRecord
        from apps.whatsapp.tasks import provision_organization_live

        try:
            org = request.user.owned_organization
        except Exception:
            return Response({'detail': 'No organization found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            record = org.provisioning_record
        except OrgProvisioningRecord.DoesNotExist:
            return Response({'detail': 'Provisioning record not found.'}, status=status.HTTP_404_NOT_FOUND)

        if record.operational_mode != 'failed':
            return Response(
                {'detail': 'Retry is only allowed when operational_mode is failed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provision_organization_live.delay(str(org.id))
        return Response({'detail': 'Provisioning retry queued.'}, status=status.HTTP_202_ACCEPTED)
