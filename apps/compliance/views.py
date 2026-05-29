import csv
import hashlib
import io
import logging

from django.http import Http404, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.core.permissions import IsAdminUser, IsAdminOrOrgAdmin, get_user_org

from .models import (
    ConsentRecord, DataExportRequest, DataDeletionRequest,
    PIIDetectionEvent, PrivacyAuditLog,
    ComplianceSanctionRecord, SanctionScreeningResult,
)
from .utils import org_is_gdpr_jurisdiction, get_request_org

logger = logging.getLogger(__name__)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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


# ── Privacy compliance views ────────────────────────────────────────────────

class PrivacyAuditLogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role == 'agent':
            return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        qs = PrivacyAuditLog.objects.order_by('-created_at')
        if user.role == 'developer':
            org = get_user_org(user)
            qs = qs.filter(org=org)

        action = request.query_params.get('action')
        if action:
            qs = qs.filter(action=action)
        jurisdiction = request.query_params.get('jurisdiction')
        if jurisdiction:
            qs = qs.filter(jurisdiction=jurisdiction)

        data = list(qs.values(
            'id', 'action', 'actor_id', 'subject_identifier',
            'jurisdiction', 'regulation', 'details', 'is_sensitive', 'created_at',
        )[:200])
        return Response(data)


class RightToBeForgottenView(APIView):
    permission_classes = [IsAdminOrOrgAdmin]

    def post(self, request):
        import re
        phone = request.data.get('phone_e164', '').strip()
        reason = request.data.get('reason', '')

        if not re.match(r'^\+\d{7,15}$', phone):
            return Response({'detail': 'Invalid E.164 phone number.'}, status=status.HTTP_400_BAD_REQUEST)

        org = get_user_org(request.user)
        if request.user.role == 'developer' and org is None:
            return Response({'detail': 'No organization found.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from apps.compliance.erasure import RTBFOrchestrator
            req = RTBFOrchestrator().initiate(phone, org, requested_by_user=request.user)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        from apps.compliance.tasks import execute_rtbf_erasure
        execute_rtbf_erasure.delay(str(req.id))

        return Response({'request_id': str(req.id)}, status=status.HTTP_202_ACCEPTED)


class RTBFStatusView(APIView):
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request, request_id):
        try:
            req = DataDeletionRequest.objects.get(id=request_id)
        except DataDeletionRequest.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if request.user.role == 'developer':
            org = get_user_org(request.user)
            user_org = getattr(req.user, 'owned_organization', None)
            if user_org != org:
                return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'id': str(req.id),
            'status': req.status,
            'regulation': req.regulation,
            'request_source': req.request_source,
            'requested_at': req.requested_at,
            'completed_at': req.completed_at,
            'erasure_scope': req.erasure_scope,
        })


class PrivacyAuditExportView(APIView):
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        user = request.user
        qs = PrivacyAuditLog.objects.order_by('-created_at')

        if user.role == 'developer':
            org = get_user_org(user)
            qs = qs.filter(org=org)
        elif user.role == 'admin':
            org_id = request.query_params.get('org')
            if org_id:
                qs = qs.filter(org_id=org_id)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['id', 'action', 'subject_identifier', 'jurisdiction',
                         'regulation', 'is_sensitive', 'created_at'])
        for row in qs.values_list('id', 'action', 'subject_identifier',
                                   'jurisdiction', 'regulation', 'is_sensitive', 'created_at'):
            writer.writerow(row)

        response = HttpResponse(buf.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="privacy_audit.csv"'
        return response


class PIIDetectionSummaryView(APIView):
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        from django.db.models import Count
        from django.utils import timezone as tz
        from datetime import timedelta

        user = request.user
        cutoff = tz.now() - timedelta(hours=24)
        qs = PIIDetectionEvent.objects.filter(detected_at__gte=cutoff)

        if user.role == 'developer':
            org = get_user_org(user)
            qs = qs.filter(org=org)

        summary = dict(qs.values_list('pattern_name').annotate(count=Count('id')).values_list('pattern_name', 'count'))
        return Response(summary)


# ── AML / Sanction compliance views ─────────────────────────────────────────

class ComplianceScreeningListView(APIView):
    """GET /compliance/screenings/ — list screening results scoped to org."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role == 'agent':
            return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        qs = SanctionScreeningResult.objects.order_by('-screened_at')
        if user.role == 'developer':
            org = get_user_org(user)
            qs = qs.filter(org=org)

        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        date_from = request.query_params.get('date_from')
        if date_from:
            qs = qs.filter(screened_at__date__gte=date_from)

        data = list(qs.values(
            'screening_id', 'screened_name', 'id_number_prefix',
            'list_source', 'match_type', 'risk_score',
            'deal_lock_id', 'org_id', 'status', 'screened_at',
        )[:200])
        return Response(data)


class ComplianceScreeningDetailView(APIView):
    """GET /compliance/screenings/<uuid:screening_id>/ — single result."""
    permission_classes = [IsAuthenticated]

    def get(self, request, screening_id):
        user = request.user
        if user.role == 'agent':
            return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            sr = SanctionScreeningResult.objects.get(screening_id=screening_id)
        except SanctionScreeningResult.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if user.role == 'developer':
            org = get_user_org(user)
            if sr.org != org:
                return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'screening_id':     str(sr.screening_id),
            'screened_name':    sr.screened_name,
            'id_number_prefix': sr.id_number_prefix,
            'list_source':      sr.list_source,
            'match_type':       sr.match_type,
            'risk_score':       sr.risk_score,
            'deal_lock_id':     str(sr.deal_lock_id) if sr.deal_lock_id else None,
            'org_id':           str(sr.org_id) if sr.org_id else None,
            'status':           sr.status,
            'screened_at':      sr.screened_at,
        })


class ComplianceSanctionListView(APIView):
    """GET /compliance/sanctions/ — admin only list; POST — admin only create."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = ComplianceSanctionRecord.objects.filter(is_active=True).order_by('-created_at')
        data = list(qs.values(
            'id', 'name', 'id_number_prefix', 'id_type',
            'list_source', 'risk_level', 'org_id', 'is_active', 'created_at',
        )[:500])
        return Response(data)

    def post(self, request):
        import hashlib as _hl
        name       = (request.data.get('name') or '').strip()
        id_number  = (request.data.get('id_number') or '').strip()
        id_type    = request.data.get('id_type', '')
        list_source = request.data.get('list_source', 'LOCAL')
        risk_level = request.data.get('risk_level', 'high')

        if not name:
            return Response({'detail': 'name is required.'}, status=status.HTTP_400_BAD_REQUEST)

        prefix   = id_number[:4] if id_number else ''
        id_hash  = _hl.sha256(id_number.encode()).hexdigest() if id_number else ''

        record = ComplianceSanctionRecord.objects.create(
            name=name,
            id_number_prefix=prefix,
            id_number_hash=id_hash,
            id_type=id_type,
            list_source=list_source,
            risk_level=risk_level,
            added_by=request.user,
        )
        return Response({'id': str(record.id)}, status=status.HTTP_201_CREATED)


class ComplianceSanctionDetailView(APIView):
    """PATCH/DELETE /compliance/sanctions/<id>/ — admin only."""
    permission_classes = [IsAdminUser]

    def patch(self, request, record_id):
        try:
            rec = ComplianceSanctionRecord.objects.get(id=record_id)
        except ComplianceSanctionRecord.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        for field in ('name', 'list_source', 'risk_level', 'id_type'):
            if field in request.data:
                setattr(rec, field, request.data[field])
        rec.save()
        return Response({'id': str(rec.id), 'is_active': rec.is_active})

    def delete(self, request, record_id):
        try:
            rec = ComplianceSanctionRecord.objects.get(id=record_id)
        except ComplianceSanctionRecord.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        rec.is_active = False
        rec.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class ComplianceReportExportView(APIView):
    """GET /compliance/export/ — org-scoped CSV of screening results."""
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        user = request.user
        qs = SanctionScreeningResult.objects.order_by('-screened_at')

        if user.role == 'developer':
            org = get_user_org(user)
            qs = qs.filter(org=org)
        elif user.role == 'admin':
            org_id = request.query_params.get('org')
            if org_id:
                qs = qs.filter(org_id=org_id)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'screening_id', 'screened_name', 'id_number_prefix',
            'list_source', 'match_type', 'risk_score', 'status', 'screened_at',
        ])
        for row in qs.values_list(
            'screening_id', 'screened_name', 'id_number_prefix',
            'list_source', 'match_type', 'risk_score', 'status', 'screened_at',
        ):
            writer.writerow(row)

        response = HttpResponse(buf.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="compliance_screenings.csv"'
        return response
