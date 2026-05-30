import json
import logging

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from datetime import timedelta
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Verification, DocumentScan, FraudBlacklist
from .serializers import VerificationSerializer, DocumentScanSerializer
from .services import FraudCheckService, VerificationSignalService
from .tasks import notify_verification_status_change
from apps.core.throttles import FraudCheckThrottle, BulkOperationThrottle

logger = logging.getLogger(__name__)


class IsAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'admin'


# ── Fraud check (existing, unchanged) ────────────────────────────────────────

class FraudCheckView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [FraudCheckThrottle]

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
        role = request.user.role
        if not self._is_dashboard(request.user):
            return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        qs = (
            Verification.objects
            .select_related('property', 'requested_by', 'reviewer')
            .prefetch_related('document_scans')
        )

        # Scope by role: agents see only their own property verifications;
        # developers see only their org's; admins see all.
        if role == 'agent':
            try:
                qs = qs.filter(property__assigned_agent=request.user.agent_profile)
            except Exception:
                qs = qs.none()
        elif role == 'developer':
            try:
                org = request.user.owned_organization
                qs = qs.filter(property__organization=org)
            except Exception:
                qs = qs.none()
        # admin: no additional filter

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

        notify_verification_status_change.delay(str(verification.pk))

        if new_status == Verification.Status.PASSED:
            from .tasks import generate_certificate_task
            generate_certificate_task.delay(str(verification.pk))

        return Response(VerificationSerializer(verification).data)


# ── Document scans ────────────────────────────────────────────────────────────

class DocumentScanListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        role = request.user.role
        if role not in ('admin', 'agent', 'developer'):
            return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        qs = DocumentScan.objects.select_related('user', 'verification').all()

        if role == 'agent':
            try:
                from apps.properties.models import Property
                agent_prop_ids = Property.objects.filter(
                    owner=request.user
                ).values_list('id', flat=True)
                qs = qs.filter(verification__property_id__in=agent_prop_ids)
            except Exception:
                qs = qs.none()
        elif role == 'developer':
            try:
                org = request.user.owned_organization
                qs = qs.filter(verification__property__organization=org)
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


class DocumentScanDetailView(APIView):
    """GET /verification/documents/<id>/ — single document scan detail."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        role = request.user.role
        if role not in ('admin', 'agent', 'developer'):
            return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            scan = DocumentScan.objects.select_related('user', 'verification').get(pk=pk)
        except DocumentScan.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if role == 'agent':
            from apps.properties.models import Property
            agent_prop_ids = Property.objects.filter(owner=request.user).values_list('id', flat=True)
            if not DocumentScan.objects.filter(
                pk=pk, verification__property_id__in=agent_prop_ids
            ).exists():
                return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        elif role == 'developer':
            try:
                org = request.user.owned_organization
            except Exception:
                return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
            if not DocumentScan.objects.filter(
                pk=pk, verification__property__organization=org
            ).exists():
                return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        return Response(DocumentScanSerializer(scan).data)


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
        try:
            limit = min(int(request.query_params.get('limit', 50)), 200)
        except (ValueError, TypeError):
            return Response({'detail': 'limit must be an integer.'}, status=400)

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
        try:
            ttl_days = int(request.data.get('ttl_days') or 0)
        except (ValueError, TypeError):
            return Response({'detail': 'ttl_days must be an integer.'}, status=400)
        if ttl_days > 3650:
            return Response({'detail': 'ttl_days cannot exceed 3650 (10 years).'}, status=400)
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


class BulkRejectVerificationsView(APIView):
    """
    POST /verification/bulk-reject/
    Body: {"verification_ids": ["uuid1", ...], "notes": "reason"}
    Admin-only: reject multiple pending verifications in one call.
    """
    permission_classes = [IsAdmin]
    throttle_classes = [BulkOperationThrottle]

    def post(self, request):
        ids   = request.data.get('verification_ids', [])
        notes = request.data.get('notes', '')

        if not ids:
            return Response({'error': 'verification_ids (list) is required.'}, status=400)

        qs = Verification.objects.filter(
            id__in=ids,
            status=Verification.Status.PENDING,
        ).select_related('property')

        count = 0
        for v in qs:
            v.status      = Verification.Status.FAILED
            v.notes       = notes
            v.reviewer    = request.user
            v.verified_at = timezone.now()
            v.save()

            if v.property:
                v.property.legal_status = 'unverified'
                v.property.save(update_fields=['legal_status'])

            notify_verification_status_change.delay(str(v.pk))
            count += 1

        return Response({'rejected': count})


class TrustCertificateView(APIView):
    """GET /verification/<property_id>/certificate/ — return trust certificate URL."""
    permission_classes = [IsAuthenticated]

    def get(self, request, property_id):
        role = request.user.role

        if role == 'agent':
            return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            from apps.properties.models import Property
            prop = Property.objects.select_related('organization').get(pk=property_id)
        except Property.DoesNotExist:
            return Response({'detail': 'Property not found.'}, status=status.HTTP_404_NOT_FOUND)

        if role == 'developer':
            try:
                org = request.user.owned_organization
            except Exception:
                return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)
            if prop.organization_id != org.pk:
                return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        verification = (
            Verification.objects
            .filter(property=prop, status=Verification.Status.PASSED)
            .order_by('-verified_at')
            .first()
        )

        if not verification:
            return Response(
                {'detail': 'No passed verification found for this property.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not verification.certificate_url:
            return Response(
                {'detail': 'Certificate is being generated.'},
                status=status.HTTP_202_ACCEPTED,
            )

        return Response({
            'property_id':      str(property_id),
            'certificate_url':  verification.certificate_url,
            'verified_at':      verification.verified_at.isoformat() if verification.verified_at else None,
            'signal_score':     verification.signal_score,
        })


# ── ID Verification (Jumio / Onfido / Stripe Identity) ───────────────────────

class IDVerificationSessionView(APIView):
    """
    POST /verification/id-verify/
    Create a hosted ID-verification session for the current user's org market.
    Returns session_url to redirect the user to the provider's hosted page.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.markets.registry import get_verification_provider
        from apps.core.permissions import get_user_org

        org_country = 'PK'
        try:
            org = get_user_org(request.user)
            if org:
                org_country = (getattr(org, 'country', 'PK') or 'PK').upper()
        except Exception:
            pass

        provider = get_verification_provider(org_country)

        if not provider.supported:
            return Response(
                {
                    'supported': False,
                    'detail': (
                        f'ID verification is not available for your market ({org_country}). '
                        'Use the WhatsApp document flow instead.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not provider.is_configured():
            logger.warning('IDVerificationSessionView: %s not configured for country=%s', provider.name, org_country)
            return Response(
                {'supported': False, 'detail': 'Verification provider credentials are not configured. Contact support.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        doc_type     = request.data.get('doc_type', 'passport')
        redirect_url = request.data.get('redirect_url', '')

        try:
            session = provider.create_session(
                user_id      = str(request.user.id),
                doc_type     = doc_type,
                redirect_url = redirect_url,
            )
        except Exception as exc:
            logger.error('IDVerificationSessionView: %s session creation failed: %s', provider.name, exc)
            return Response(
                {'detail': 'Verification service error. Please try again.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Record the session so the webhook can look it up later
        DocumentScan.objects.create(
            user              = request.user,
            document_type     = DocumentScan.DocType.PASSPORT if 'passport' in doc_type.lower() else DocumentScan.DocType.OTHER,
            registration_number = session.session_id,
            status            = DocumentScan.Status.UNREADABLE,
            extracted_fields  = {'provider': provider.name, 'session_id': session.session_id},
        )

        return Response({
            'supported':   True,
            'provider':    session.provider,
            'session_id':  session.session_id,
            'session_url': session.session_url,
        }, status=status.HTTP_201_CREATED)


def _apply_verification_result(session_id: str, result) -> bool:
    """Find DocumentScan by session_id and update its status. Returns True if found."""
    scan = DocumentScan.objects.filter(registration_number=session_id).first()
    if not scan:
        return False
    scan.status = DocumentScan.Status.CLEAN if result.status == 'approved' else DocumentScan.Status.SUSPICIOUS
    scan.extracted_fields = {**scan.extracted_fields, **result.extracted_fields, 'verification_status': result.status}
    scan.red_flags        = result.red_flags
    scan.confidence       = 'HIGH' if result.confidence >= 0.8 else ('MEDIUM' if result.confidence >= 0.5 else 'LOW')
    scan.save(update_fields=['status', 'extracted_fields', 'red_flags', 'confidence'])
    return True


@method_decorator(csrf_exempt, name='dispatch')
class JumioWebhookView(APIView):
    """POST /verification/webhook/jumio/ — Jumio callback when verification completes."""
    permission_classes  = [AllowAny]
    authentication_classes = []

    def post(self, request):
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return Response({'detail': 'Bad JSON'}, status=status.HTTP_400_BAD_REQUEST)

        from .providers.jumio_provider import JumioVerificationProvider
        provider    = JumioVerificationProvider()
        session_id, result = provider.parse_webhook(payload)

        if not session_id:
            return Response({'received': True})

        found = _apply_verification_result(session_id, result)
        logger.info('JumioWebhookView: session=%s status=%s found=%s', session_id, result.status, found)
        return Response({'received': True, 'activated': found})


@method_decorator(csrf_exempt, name='dispatch')
class OnfidoWebhookView(APIView):
    """POST /verification/webhook/onfido/ — Onfido check.completed callback."""
    permission_classes  = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw = request.body
        sig = request.headers.get('X-SHA2-Signature', '')

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return Response({'detail': 'Bad JSON'}, status=status.HTTP_400_BAD_REQUEST)

        from .providers.onfido_provider import OnfidoVerificationProvider
        provider = OnfidoVerificationProvider()

        if not provider.verify_webhook_signature(raw, sig):
            logger.warning('OnfidoWebhookView: invalid signature')
            return Response({'detail': 'Invalid signature.'}, status=status.HTTP_400_BAD_REQUEST)

        session_id, result = provider.parse_webhook(payload)
        found = _apply_verification_result(session_id, result) if session_id else False
        logger.info('OnfidoWebhookView: session=%s status=%s found=%s', session_id, result.status, found)
        return Response({'received': True, 'activated': found})


@method_decorator(csrf_exempt, name='dispatch')
class StripeIdentityWebhookView(APIView):
    """POST /verification/webhook/stripe-identity/ — Stripe Identity verification webhook."""
    permission_classes  = [AllowAny]
    authentication_classes = []

    def post(self, request):
        import stripe
        from django.conf import settings

        raw = request.body
        sig = request.headers.get('Stripe-Signature', '')

        try:
            stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
            secret = getattr(settings, 'STRIPE_IDENTITY_WEBHOOK_SECRET', '')
            event  = stripe.Webhook.construct_event(raw, sig, secret)
        except stripe.error.SignatureVerificationError:
            logger.warning('StripeIdentityWebhookView: invalid signature')
            return Response({'detail': 'Invalid signature.'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error('StripeIdentityWebhookView: parse error: %s', exc)
            return Response({'detail': 'Bad request.'}, status=status.HTTP_400_BAD_REQUEST)

        if event['type'] not in ('identity.verification_session.verified',
                                  'identity.verification_session.requires_input'):
            return Response({'received': True})

        from .providers.stripe_identity_provider import StripeIdentityProvider
        provider = StripeIdentityProvider()
        session_id, result = provider.parse_webhook(dict(event))
        found = _apply_verification_result(session_id, result) if session_id else False
        logger.info('StripeIdentityWebhookView: session=%s status=%s found=%s', session_id, result.status, found)
        return Response({'received': True, 'activated': found})
