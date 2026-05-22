from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.http import Http404
from django.utils import timezone

from .models import ConsentRecord, DataExportRequest, DataDeletionRequest
from .utils import org_is_gdpr_jurisdiction, get_request_org


def _require_gdpr(request):
    """Raise Http404 if the request's org is not in a GDPR jurisdiction."""
    org = get_request_org(request)
    if not org or not org_is_gdpr_jurisdiction(org):
        raise Http404


class ConsentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _require_gdpr(request)
        purpose = request.data.get('purpose')
        revoke  = request.data.get('revoke', False)
        valid_purposes = [c.value for c in ConsentRecord.Purpose]
        if purpose not in valid_purposes:
            return Response(
                {'detail': f"Invalid purpose. Choose from: {valid_purposes}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record, _ = ConsentRecord.objects.get_or_create(
            user=request.user,
            purpose=purpose,
            defaults={'is_active': not revoke},
        )
        if revoke:
            record.is_active  = False
            record.revoked_at = timezone.now()
        else:
            record.is_active  = True
            record.revoked_at = None
        record.save(update_fields=['is_active', 'revoked_at'])
        return Response({'purpose': purpose, 'is_active': record.is_active})


class DataExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_gdpr(request)
        from .tasks import generate_data_export
        req = DataExportRequest.objects.create(user=request.user)
        generate_data_export.delay(str(req.id))
        return Response(
            {'id': str(req.id), 'status': req.status},
            status=status.HTTP_202_ACCEPTED,
        )


class DataDeletionView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        _require_gdpr(request)
        from .tasks import execute_data_deletion
        req = DataDeletionRequest.objects.create(user=request.user)
        execute_data_deletion.delay(str(req.id))
        return Response(
            {'id': str(req.id), 'status': req.status},
            status=status.HTTP_202_ACCEPTED,
        )
