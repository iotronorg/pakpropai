import hashlib
import hmac
import json
import logging
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import WhatsAppSession, WhatsAppMessage
from .router import MessageRouter

logger = logging.getLogger(__name__)


class WhatsAppWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    # --- GET: Meta verifies us once on setup --------------------------
    def get(self, request):
        mode      = request.GET.get('hub.mode')
        token     = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        if mode == 'subscribe' and token == settings.WA_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type='text/plain')
        return HttpResponse(status=403)

    # --- POST: Meta delivers messages here ----------------------------
    def post(self, request):
        # 1. Verify signature
        signature = request.headers.get('X-Hub-Signature-256', '')
        if not self._is_signature_valid(request.body, signature):
            logger.warning("WA webhook: invalid signature")
            return Response(status=403)

        try:
            payload = json.loads(request.body.decode())
        except json.JSONDecodeError:
            return Response(status=400)

        # 2. Process every message in the payload
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                for message in value.get('messages', []) or []:
                    self._process_with_idempotency(message)

        # Always 200 — Meta will retry otherwise
        return Response({'status': 'ok'})

    @staticmethod
    def _is_signature_valid(body: bytes, signature: str) -> bool:
        if not settings.WA_APP_SECRET:
            # Dev mode fallback — accept all
            return True
        if not signature.startswith('sha256='):
            return False
        expected = 'sha256=' + hmac.new(
            settings.WA_APP_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def _process_with_idempotency(message: dict):
        msg_id = message.get('id')
        if not msg_id:
            return

        # 48-hour idempotency: if we've seen this message ID, skip
        idem_key = f"wa:processed:{msg_id}"
        if cache.get(idem_key):
            logger.info(f"Skipping duplicate WA message {msg_id}")
            return
        cache.set(idem_key, True, 60 * 60 * 48)

        phone = message.get('from')
        if not phone:
            return

        try:
            MessageRouter.route(message, phone)
        except Exception:
            logger.exception(f"Router crashed on message {msg_id}")


# ── Notifications / WhatsApp history (admin/agent/developer) ─────────────────

class _IsDashboard(IsAuthenticated):
    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.role in ('admin', 'agent', 'developer')
        )


class NotificationListView(APIView):
    """
    GET /notifications/
    Returns paginated WhatsApp sessions with metadata.
    Admins see all; agents see sessions for their assigned leads only;
    developers see sessions for their org leads.

    Query params:
      phone=<number>   — filter by phone (prefix match)
      state=<state>    — filter by session state
      limit=<n>        — page size (default 50, max 200)
      offset=<n>       — offset for pagination
    """
    permission_classes = [_IsDashboard]

    def get(self, request):
        role = request.user.role
        qs = (
            WhatsAppSession.objects
            .select_related('user')
            .prefetch_related('messages')
            .order_by('-last_message_at')
        )

        if role == 'agent':
            try:
                agent_lead_phones = (
                    request.user.agent_profile.leads
                    .values_list('phone', flat=True)
                )
                qs = qs.filter(phone__in=agent_lead_phones)
            except Exception:
                qs = qs.none()
        elif role == 'developer':
            try:
                from apps.leads.models import Lead
                org = request.user.agent_profile
                org_lead_phones = Lead.objects.filter(
                    assigned_agent__parent_organization=org
                ).values_list('phone', flat=True)
                qs = qs.filter(phone__in=org_lead_phones)
            except Exception:
                qs = qs.none()

        # Filters
        if phone := request.query_params.get('phone'):
            qs = qs.filter(phone__startswith=phone.lstrip('+'))
        if state := request.query_params.get('state'):
            qs = qs.filter(state=state)

        # Pagination
        limit  = min(int(request.query_params.get('limit',  50)), 200)
        offset = max(int(request.query_params.get('offset', 0)),  0)
        total  = qs.count()
        page   = qs[offset: offset + limit]

        results = [
            {
                'id':             str(s.id),
                'phone':          s.phone,
                'user_id':        str(s.user_id) if s.user_id else None,
                'state':          s.state,
                'message_count':  s.message_count,
                'started_at':     s.started_at.isoformat(),
                'last_message_at': s.last_message_at.isoformat(),
            }
            for s in page
        ]

        return Response({
            'count':   total,
            'limit':   limit,
            'offset':  offset,
            'results': results,
        })


class NotificationDetailView(APIView):
    """
    GET /notifications/<session_id>/
    Returns full message thread for a WhatsApp session.

    Query params:
      direction=inbound|outbound
      limit=<n>  (default 100, max 500)
      offset=<n>
    """
    permission_classes = [_IsDashboard]

    def get(self, request, pk):
        try:
            session = WhatsAppSession.objects.select_related('user').get(pk=pk)
        except WhatsAppSession.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=404)

        role = request.user.role
        if role == 'agent':
            try:
                agent_lead_phones = list(
                    request.user.agent_profile.leads.values_list('phone', flat=True)
                )
                if session.phone not in agent_lead_phones:
                    return Response({'detail': 'Not authorized.'}, status=403)
            except Exception:
                return Response({'detail': 'Not authorized.'}, status=403)
        elif role == 'developer':
            try:
                from apps.leads.models import Lead
                org = request.user.agent_profile
                org_phones = list(
                    Lead.objects.filter(
                        assigned_agent__parent_organization=org
                    ).values_list('phone', flat=True)
                )
                if session.phone not in org_phones:
                    return Response({'detail': 'Not authorized.'}, status=403)
            except Exception:
                return Response({'detail': 'Not authorized.'}, status=403)

        msgs_qs = session.messages.order_by('created_at')

        if direction := request.query_params.get('direction'):
            msgs_qs = msgs_qs.filter(direction=direction)

        limit  = min(int(request.query_params.get('limit',  100)), 500)
        offset = max(int(request.query_params.get('offset', 0)),   0)
        total  = msgs_qs.count()
        page   = msgs_qs[offset: offset + limit]

        messages = [
            {
                'id':           str(m.id),
                'wa_message_id': m.wa_message_id,
                'direction':    m.direction,
                'msg_type':     m.msg_type,
                'body':         m.body,
                'media_url':    m.media_url,
                'created_at':   m.created_at.isoformat(),
            }
            for m in page
        ]

        return Response({
            'session': {
                'id':             str(session.id),
                'phone':          session.phone,
                'user_id':        str(session.user_id) if session.user_id else None,
                'state':          session.state,
                'message_count':  session.message_count,
                'started_at':     session.started_at.isoformat(),
                'last_message_at': session.last_message_at.isoformat(),
            },
            'messages': {
                'count':   total,
                'limit':   limit,
                'offset':  offset,
                'results': messages,
            },
        })