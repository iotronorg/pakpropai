from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Verification, DocumentScan
from .serializers import VerificationSerializer, DocumentScanSerializer
from .services import FraudCheckService, VerificationSignalService


class IsAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'admin'


# ── Fraud check (existing, unchanged) ────────────────────────────────────────

class FraudCheckView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        query = (request.data.get('query') or '').strip()
        if not query:
            return Response({'error': 'query is required'}, status=400)
        if len(query) > 500:
            return Response({'error': 'query too long (max 500 chars)'}, status=400)
        result = FraudCheckService.check(query, user=request.user)
        return Response(result)


# ── Verification queue ────────────────────────────────────────────────────────

class VerificationQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def _is_dashboard(self, user):
        return user.role in ('admin', 'agent', 'developer')

    def get(self, request):
        if not self._is_dashboard(request.user):
            return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        qs = (
            Verification.objects
            .select_related('property', 'requested_by', 'reviewer')
            .prefetch_related('document_scans')
            .all()
        )

        # filter by status if requested
        s = request.query_params.get('status')
        if s:
            qs = qs.filter(status=s)

        serializer = VerificationSerializer(qs, many=True)
        return Response({'count': qs.count(), 'results': serializer.data})


class VerificationReviewView(APIView):
    """Admin-only: approve or reject a verification and update property legal_status."""
    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        try:
            verification = (
                Verification.objects
                .select_related('property')
                .prefetch_related('document_scans')
                .get(pk=pk)
            )
        except Verification.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get('status')
        if new_status not in (Verification.Status.PASSED, Verification.Status.FAILED,
                               Verification.Status.DISPUTED):
            return Response(
                {'error': 'status must be passed, failed, or disputed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        verification.status      = new_status
        verification.notes       = request.data.get('notes', verification.notes)
        verification.reviewer    = request.user
        verification.verified_at = timezone.now()
        verification.signal_score = VerificationSignalService.compute_score(verification)
        verification.save()

        # mirror result onto the property legal_status
        prop = verification.property
        if new_status == Verification.Status.PASSED:
            prop.legal_status = 'verified'
        elif new_status == Verification.Status.FAILED:
            prop.legal_status = 'unverified'
        elif new_status == Verification.Status.DISPUTED:
            prop.legal_status = 'disputed'
        prop.save(update_fields=['legal_status'])

        return Response(VerificationSerializer(verification).data)


# ── Document scans ────────────────────────────────────────────────────────────

class DocumentScanListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ('admin', 'agent', 'developer'):
            return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        qs = DocumentScan.objects.select_related('user', 'verification').all()

        doc_type = request.query_params.get('document_type')
        if doc_type:
            qs = qs.filter(document_type=doc_type)
        scan_status = request.query_params.get('status')
        if scan_status:
            qs = qs.filter(status=scan_status)

        serializer = DocumentScanSerializer(qs, many=True)
        return Response({'count': qs.count(), 'results': serializer.data})


class LinkDocumentToVerificationView(APIView):
    """Link a DocumentScan to a Verification and recompute signal score."""
    permission_classes = [IsAdmin]

    def post(self, request, scan_id, verification_id):
        try:
            scan = DocumentScan.objects.get(pk=scan_id)
        except DocumentScan.DoesNotExist:
            return Response({'error': 'Document scan not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            verification = (
                Verification.objects
                .prefetch_related('document_scans')
                .get(pk=verification_id)
            )
        except Verification.DoesNotExist:
            return Response({'error': 'Verification not found.'}, status=status.HTTP_404_NOT_FOUND)

        scan.verification = verification
        scan.save(update_fields=['verification'])

        # recompute signal score with the newly linked document
        VerificationSignalService.refresh(verification)

        return Response(VerificationSerializer(
            Verification.objects.prefetch_related('document_scans')
            .select_related('property', 'requested_by', 'reviewer')
            .get(pk=verification_id)
        ).data)
