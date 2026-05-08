from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Verification, DocumentScan, FraudBlacklist
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
        role = request.user.role
        if role not in ('admin', 'agent', 'developer'):
            return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        qs = DocumentScan.objects.select_related('user', 'verification').all()

        # Agents only see scans for their own properties' verifications
        if role == 'agent':
            try:
                from apps.properties.models import Property
                agent_prop_ids = Property.objects.filter(
                    owner=request.user
                ).values_list('id', flat=True)
                qs = qs.filter(verification__property_id__in=agent_prop_ids)
            except Exception:
                qs = qs.none()

        # Filters
        if verification_id := request.query_params.get('verification'):
            qs = qs.filter(verification_id=verification_id)
        if doc_type := request.query_params.get('document_type'):
            qs = qs.filter(document_type=doc_type)
        if scan_status := request.query_params.get('status'):
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


# ── Fraud Monitoring Dashboard APIs ─────────────────────────────────────────

class FraudStatsView(APIView):
    """GET /verification/fraud/stats/ — aggregate numbers for the dashboard."""
    permission_classes = [IsAdmin]

    def get(self, request):
        now     = timezone.now()
        day30   = now - timedelta(days=30)
        day7    = now - timedelta(days=7)

        suspicious_scans  = DocumentScan.objects.filter(status='suspicious').count()
        red_flag_scans    = DocumentScan.objects.exclude(red_flags=[]).count()
        disputed_props    = 0
        high_risk_props   = 0
        try:
            from apps.properties.models import Property
            disputed_props  = Property.objects.filter(legal_status='disputed').count()
            high_risk_props = Property.objects.filter(risk_level='high').count()
        except Exception:
            pass

        fraud_verifications = Verification.objects.exclude(fraud_flags=[]).count()
        blacklist_count     = FraudBlacklist.objects.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).count()

        recent_suspicious = DocumentScan.objects.filter(
            status='suspicious', created_at__gte=day7
        ).count()
        recent_fraud_verifs = Verification.objects.filter(
            created_at__gte=day30
        ).exclude(fraud_flags=[]).count()

        return Response({
            'suspicious_scans':      suspicious_scans,
            'red_flag_scans':        red_flag_scans,
            'fraud_verifications':   fraud_verifications,
            'disputed_properties':   disputed_props,
            'high_risk_properties':  high_risk_props,
            'blacklisted_tokens':    blacklist_count,
            'alerts_last_7_days':    recent_suspicious + recent_fraud_verifs,
        })


class FraudAlertsView(APIView):
    """GET /verification/fraud/alerts/ — chronological feed of fraud signals."""
    permission_classes = [IsAdmin]

    def get(self, request):
        limit = min(int(request.query_params.get('limit', 50)), 200)

        # suspicious document scans
        sus_scans = (
            DocumentScan.objects
            .filter(Q(status='suspicious') | ~Q(red_flags=[]))
            .select_related('user')
            .order_by('-created_at')[:limit]
        )
        scan_alerts = [
            {
                'id':        s.id,
                'type':      'document_scan',
                'severity':  'high' if s.status == 'suspicious' else 'medium',
                'phone':     s.phone or (s.user.phone if s.user else ''),
                'detail':    f"{s.get_document_type_display()} — {len(s.red_flags)} red flag(s): {', '.join(s.red_flags[:3])}",
                'owner':     s.owner_name,
                'created_at': s.created_at.isoformat(),
            }
            for s in sus_scans
        ]

        # verifications with fraud flags
        fraud_verifs = (
            Verification.objects
            .exclude(fraud_flags=[])
            .select_related('property', 'requested_by')
            .order_by('-created_at')[:limit]
        )
        verif_alerts = [
            {
                'id':        str(v.id),
                'type':      'verification',
                'severity':  'high',
                'phone':     v.requested_by.phone if v.requested_by else '',
                'detail':    f"Verification fraud flags: {', '.join(str(f) for f in v.fraud_flags[:3])}",
                'owner':     v.property.title,
                'created_at': v.created_at.isoformat(),
            }
            for v in fraud_verifs
        ]

        # high-risk properties (recent)
        prop_alerts = []
        try:
            from apps.properties.models import Property
            high_risk = (
                Property.objects
                .filter(risk_level='high')
                .select_related('owner')
                .order_by('-created_at')[:20]
            )
            prop_alerts = [
                {
                    'id':        str(p.id),
                    'type':      'property',
                    'severity':  'medium',
                    'phone':     p.owner.phone if p.owner else '',
                    'detail':    f"High-risk property — AI score: {p.ai_score}",
                    'owner':     p.title,
                    'created_at': p.created_at.isoformat(),
                }
                for p in high_risk
            ]
        except Exception:
            pass

        all_alerts = sorted(
            scan_alerts + verif_alerts + prop_alerts,
            key=lambda a: a['created_at'],
            reverse=True,
        )[:limit]

        return Response({'count': len(all_alerts), 'results': all_alerts})


class FraudBlacklistView(APIView):
    """GET/POST /verification/fraud/blacklist/ — list and add blacklist tokens."""
    permission_classes = [IsAdmin]

    def get(self, request):
        now     = timezone.now()
        entries = FraudBlacklist.objects.select_related('added_by').filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).order_by('-created_at')
        data = [
            {
                'id':         e.id,
                'token':      e.token,
                'reason':     e.reason,
                'added_by':   e.added_by.phone if e.added_by else 'system',
                'expires_at': e.expires_at.isoformat() if e.expires_at else None,
                'created_at': e.created_at.isoformat(),
            }
            for e in entries
        ]
        return Response({'count': len(data), 'results': data})

    def post(self, request):
        token      = (request.data.get('token') or '').strip().lower()
        reason     = request.data.get('reason', '')
        ttl_days   = int(request.data.get('ttl_days') or 0)
        expires_at = None

        if not token:
            return Response({'detail': 'token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        if ttl_days > 0:
            expires_at = timezone.now() + timedelta(days=ttl_days)

        entry, created = FraudBlacklist.objects.update_or_create(
            token=token,
            defaults={'reason': reason, 'added_by': request.user, 'expires_at': expires_at},
        )
        # sync_to_cache is called by model.save()

        return Response({
            'id':         entry.id,
            'token':      entry.token,
            'reason':     entry.reason,
            'expires_at': entry.expires_at.isoformat() if entry.expires_at else None,
            'created':    created,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class FraudBlacklistDeleteView(APIView):
    """DELETE /verification/fraud/blacklist/<id>/ — remove a token."""
    permission_classes = [IsAdmin]

    def delete(self, request, pk):
        try:
            entry = FraudBlacklist.objects.get(pk=pk)
        except FraudBlacklist.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        entry.delete()  # removes from Redis cache via model.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FlaggedUsersView(APIView):
    """GET /verification/fraud/users/ — users with suspicious activity."""
    permission_classes = [IsAdmin]

    def get(self, request):
        # Users who submitted suspicious document scans
        suspicious_user_phones = (
            DocumentScan.objects
            .filter(Q(status='suspicious') | ~Q(red_flags=[]))
            .values_list('phone', flat=True)
            .distinct()
        )

        # Users who own high-risk properties
        flagged_owner_ids = set()
        try:
            from apps.properties.models import Property
            flagged_owner_ids = set(
                Property.objects
                .filter(Q(risk_level='high') | Q(legal_status='disputed'))
                .values_list('owner_id', flat=True)
            )
        except Exception:
            pass

        # WhatsApp sessions with very high message counts (potential spam/probe)
        from apps.whatsapp.models import WhatsAppSession
        heavy_users = (
            WhatsAppSession.objects
            .filter(message_count__gte=50)
            .select_related('user')
            .order_by('-message_count')[:30]
        )

        result = []

        # Build from suspicious scans
        seen_phones = set()
        scan_qs = (
            DocumentScan.objects
            .filter(Q(status='suspicious') | ~Q(red_flags=[]))
            .select_related('user')
            .order_by('-created_at')
        )
        for scan in scan_qs:
            phone = scan.phone or (scan.user.phone if scan.user else '')
            if not phone or phone in seen_phones:
                continue
            seen_phones.add(phone)
            result.append({
                'phone':   phone,
                'reason':  'Suspicious document scan',
                'flags':   scan.red_flags[:3],
                'risk':    'high' if scan.status == 'suspicious' else 'medium',
                'last_seen': scan.created_at.isoformat(),
            })

        # Heavy WhatsApp users not already listed
        for session in heavy_users:
            phone = session.phone
            if phone in seen_phones:
                continue
            seen_phones.add(phone)
            result.append({
                'phone':   phone,
                'reason':  f'High message volume ({session.message_count} messages)',
                'flags':   [],
                'risk':    'low',
                'last_seen': session.last_message_at.isoformat(),
            })

        result.sort(key=lambda u: ('low', 'medium', 'high').index(u['risk']), reverse=True)
        return Response({'count': len(result), 'results': result[:100]})
