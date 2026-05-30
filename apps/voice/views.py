import logging

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminOrOrgAdmin, IsAgentOrAdmin, get_user_org
from .models import VoiceCallSession, OrgVoiceConfig
from .serializers import VoiceCallSessionSerializer, OrgVoiceConfigSerializer

logger = logging.getLogger(__name__)


def _get_full_url(request, path: str) -> str:
    """Build absolute URL for TwiML webhooks."""
    base = getattr(settings, 'BASE_URL', f'{request.scheme}://{request.get_host()}')
    return f'{base}{path}'


@method_decorator(csrf_exempt, name='dispatch')
class VoiceInboundWebhookView(APIView):
    """POST /api/v1/voice/webhook/inbound/ — Twilio calls this on every inbound call."""
    permission_classes     = [AllowAny]
    authentication_classes = []

    def post(self, request):
        call_sid   = request.data.get('CallSid', '')
        from_phone = request.data.get('From', '')
        to_phone   = request.data.get('To', '')

        if not self._validate_signature(request):
            logger.warning("VoiceInbound: invalid signature call_sid=%s", call_sid)
            return Response({'detail': 'Invalid signature.'}, status=403)

        # Resolve org from the called number
        org = None
        try:
            org = OrgVoiceConfig.objects.select_related('organization').get(
                phone_number=to_phone, is_active=True
            ).organization
        except OrgVoiceConfig.DoesNotExist:
            pass

        # Resolve lead from caller phone
        lead = None
        try:
            from apps.leads.models import Lead
            lead = Lead.objects.filter(
                user__phone=from_phone,
                **(({'organization': org}) if org else {})
            ).select_related('user').first()
        except Exception:
            pass

        # Create session
        session, _ = VoiceCallSession.objects.get_or_create(
            call_sid=call_sid,
            defaults={
                'organization': org,
                'lead':         lead,
                'from_phone':   from_phone,
                'to_phone':     to_phone,
                'direction':    VoiceCallSession.Direction.INBOUND,
                'status':       VoiceCallSession.Status.RINGING,
                'started_at':   timezone.now(),
            },
        )

        # Snapshot AI context
        from apps.voice.ai_processor import AIVoiceProcessor
        ctx = AIVoiceProcessor.build_voice_context(session)
        if ctx:
            VoiceCallSession.objects.filter(call_sid=call_sid).update(ai_context_snapshot=ctx)

        # Return TwiML to connect audio stream
        stream_url = _get_full_url(request, f'/ws/voice/stream/{call_sid}/')
        stream_url = stream_url.replace('https://', 'wss://').replace('http://', 'ws://')

        from apps.voice.providers.base import get_org_provider
        provider = get_org_provider(org) if org else None
        if provider:
            twiml = provider.generate_twiml_answer(call_sid, stream_url)
        else:
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Response>'
                f'<Connect><Stream url="{stream_url}"/></Connect>'
                '</Response>'
            )

        return HttpResponse(twiml, content_type='text/xml', status=200)

    def _validate_signature(self, request) -> bool:
        to_phone = request.data.get('To', '')
        try:
            cfg = OrgVoiceConfig.objects.get(phone_number=to_phone, is_active=True)
            from apps.voice.providers.base import get_org_provider
            provider = get_org_provider(cfg.organization)
        except Exception:
            if settings.DEBUG:
                return True
            return False

        url = _get_full_url(request, request.path)
        sig = request.headers.get('X-Twilio-Signature', '')
        return provider.validate_webhook_signature(url, dict(request.data), sig)


class VoiceOutboundInitiateView(APIView):
    """POST /api/v1/voice/calls/initiate/ — developer or admin initiates outbound call to a lead."""
    permission_classes = [IsAuthenticated, IsAdminOrOrgAdmin]

    def post(self, request):
        lead_id = request.data.get('lead_id')
        if not lead_id:
            return Response({'detail': 'lead_id required.'}, status=400)

        org = get_user_org(request.user) if request.user.role != 'admin' else None

        try:
            from apps.leads.models import Lead
            qs = Lead.objects.select_related('user')
            if org:
                qs = qs.filter(organization=org)
            lead = qs.get(id=lead_id)
        except Lead.DoesNotExist:
            return Response({'detail': 'Lead not found.'}, status=404)

        try:
            cfg = (org or lead.organization).voice_config
        except Exception:
            return Response({'detail': 'Voice not configured for this organization.'}, status=400)

        session = VoiceCallSession.objects.create(
            organization=org or lead.organization,
            lead=lead,
            call_sid=f'pending_{lead_id}',
            from_phone=cfg.phone_number,
            to_phone=lead.user.phone,
            direction=VoiceCallSession.Direction.OUTBOUND,
            status=VoiceCallSession.Status.RINGING,
            started_at=timezone.now(),
        )

        from apps.voice.providers.base import get_org_provider
        provider = get_org_provider(org or lead.organization)
        result   = provider.initiate_outbound(
            to_phone=lead.user.phone,
            from_phone=cfg.phone_number,
            org_id=str((org or lead.organization).id),
        )

        if result.success:
            session.call_sid = result.call_sid
            session.save(update_fields=['call_sid'])
            return Response({'call_sid': result.call_sid}, status=202)
        else:
            session.status = VoiceCallSession.Status.FAILED
            session.save(update_fields=['status'])
            return Response({'detail': result.error or 'Initiation failed.'}, status=502)


class VoiceBargeInView(APIView):
    """POST /api/v1/voice/calls/<call_sid>/barge-in/ — agent takes over from AI."""
    permission_classes = [IsAuthenticated, IsAgentOrAdmin]

    def post(self, request, call_sid: str):
        org = get_user_org(request.user) if request.user.role != 'admin' else None

        try:
            qs = VoiceCallSession.objects.select_related('organization')
            if org:
                qs = qs.filter(organization=org)
            session = qs.get(call_sid=call_sid)
        except VoiceCallSession.DoesNotExist:
            return Response({'detail': 'Call not found.'}, status=404)

        if session.status != VoiceCallSession.Status.AI_HANDLING:
            return Response(
                {'detail': f'Cannot barge in — call status is {session.status}.'},
                status=409,
            )

        from apps.voice.barge_in import BargeInLatencyManager
        result = BargeInLatencyManager.execute_barge_in(
            call_sid=call_sid,
            agent_user=request.user,
            org=session.organization,
        )

        if result.success:
            return Response({'latency_ms': round(result.latency_ms, 1)}, status=200)
        return Response({'detail': result.error}, status=409)


class VoiceCallListView(APIView):
    """GET /api/v1/voice/calls/ — org-scoped call history."""
    permission_classes = [IsAuthenticated, IsAdminOrOrgAdmin]

    def get(self, request):
        org = get_user_org(request.user) if request.user.role != 'admin' else None
        qs  = VoiceCallSession.objects.all()
        if org:
            qs = qs.filter(organization=org)

        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        qs = qs.order_by('-created_at')[:200]
        return Response(VoiceCallSessionSerializer(qs, many=True).data)


class VoiceCallDetailView(APIView):
    """GET /api/v1/voice/calls/<call_sid>/"""
    permission_classes = [IsAuthenticated, IsAdminOrOrgAdmin]

    def get(self, request, call_sid: str):
        org = get_user_org(request.user) if request.user.role != 'admin' else None
        try:
            qs = VoiceCallSession.objects
            if org:
                qs = qs.filter(organization=org)
            session = qs.get(call_sid=call_sid)
        except VoiceCallSession.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=404)
        return Response(VoiceCallSessionSerializer(session).data)


class OrgVoiceConfigView(APIView):
    """GET / PATCH /api/v1/voice/config/ — org voice settings."""
    permission_classes = [IsAuthenticated, IsAdminOrOrgAdmin]

    def _get_config(self, request):
        org = get_user_org(request.user) if request.user.role != 'admin' else None
        if not org:
            return None, Response({'detail': 'No organization.'}, status=400)
        cfg, _ = OrgVoiceConfig.objects.get_or_create(organization=org)
        return cfg, None

    def get(self, request):
        cfg, err = self._get_config(request)
        if err:
            return err
        return Response(OrgVoiceConfigSerializer(cfg).data)

    def patch(self, request):
        cfg, err = self._get_config(request)
        if err:
            return err
        serializer = OrgVoiceConfigSerializer(cfg, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
