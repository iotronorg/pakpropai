import hashlib
import hmac
import json
import logging
import time
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.shortcuts import get_object_or_404
from apps.core.throttles import WhatsAppWebhookThrottle
from apps.core.permissions import IsAgentOrAdmin, get_user_org
import requests as _http_requests
from .models import OrgWhatsAppConfig, WhatsAppSession, WhatsAppMessage
from .serializers import OrgWhatsAppConfigSerializer

logger = logging.getLogger(__name__)


class WhatsAppWebhookView(APIView):
    permission_classes     = [AllowAny]
    authentication_classes = []
    throttle_classes       = [WhatsAppWebhookThrottle]

    # --- GET: Meta verifies us once on setup --------------------------
    def get(self, request):
        mode      = request.GET.get('hub.mode')
        token     = request.GET.get('hub.verify_token', '')
        challenge = request.GET.get('hub.challenge', '')

        if mode != 'subscribe' or not token:
            return HttpResponse(status=403)

        # Try per-org verify_token first
        try:
            cfg = OrgWhatsAppConfig.objects.get(verify_token=token, is_active=True)
            from django.utils import timezone as _tz
            cfg.webhook_verified_at = _tz.now()
            cfg.save(update_fields=['webhook_verified_at'])
            return HttpResponse(challenge, content_type='text/plain')
        except OrgWhatsAppConfig.DoesNotExist:
            pass

        # Fallback to global platform token
        if token == settings.WA_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type='text/plain')

        return HttpResponse(status=403)

    # --- POST: Meta delivers messages here ----------------------------
    def post(self, request):
        # Phase 1: Parse payload to extract phone_number_id (stateless — no DB writes).
        # Must happen before signature check so we can look up the org's secret.
        try:
            payload = json.loads(request.body.decode())
        except json.JSONDecodeError:
            return Response(status=400)

        phone_number_id = ''
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                pnid = change.get('value', {}).get('metadata', {}).get('phone_number_id', '')
                if pnid:
                    phone_number_id = pnid
                    break
            if phone_number_id:
                break

        # Phase 2: Resolve per-org app_secret; fall back to global.
        app_secret = settings.WA_APP_SECRET
        if phone_number_id:
            try:
                cfg = OrgWhatsAppConfig.objects.get(
                    phone_number_id=phone_number_id, is_active=True
                )
                if cfg.app_secret:
                    app_secret = cfg.app_secret
            except OrgWhatsAppConfig.DoesNotExist:
                pass

        # Phase 3: Verify HMAC-SHA256 signature using the resolved secret.
        signature = request.headers.get('X-Hub-Signature-256', '')
        if not self._is_signature_valid(request.body, signature, app_secret):
            logger.warning(
                "WA webhook: invalid signature for phone_number_id=%s", phone_number_id
            )
            return Response(status=403)

        # Phase 4: Process each message.
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value        = change.get('value', {})
                msg_phone_id = value.get('metadata', {}).get('phone_number_id', '')
                for message in value.get('messages', []) or []:
                    self._process_with_idempotency(message, msg_phone_id)

        return Response({'status': 'ok'})

    @staticmethod
    def _is_signature_valid(body: bytes, signature: str, secret: str) -> bool:
        if not secret:
            if settings.DEBUG:
                return True
            logger.error("WA webhook: no app_secret configured — rejecting all requests")
            return False
        if not signature.startswith('sha256='):
            return False
        expected = 'sha256=' + hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # Max messages processed per phone number per minute. Excess are dropped
    # silently (Meta still gets 200 so it won't retry).
    _RATE_LIMIT_PER_MIN: int = getattr(settings, 'WA_RATE_LIMIT_PER_MINUTE', 15)

    @staticmethod
    def _is_phone_rate_limited(phone: str, limit: int) -> bool:
        minute = int(time.time() // 60)
        key = f"wa:rate:{phone}:{minute}"
        cache.add(key, 0, 70)   # initialise to 0 with 70 s TTL if key is new
        try:
            count = cache.incr(key)
        except ValueError:
            count = 1
        return count > limit

    @classmethod
    def _process_with_idempotency(cls, message: dict, phone_number_id: str = ''):
        msg_id = message.get('id')
        phone  = message.get('from')
        if not msg_id or not phone:
            return

        # Gateway-level rate limit: drop obvious floods before queuing a task
        if cls._is_phone_rate_limited(phone, cls._RATE_LIMIT_PER_MIN):
            logger.warning("WA gateway rate limit for phone %s — dropped msg %s", phone, msg_id)
            return

        # Atomic 48h idempotency guard: cache.add returns False if key already exists.
        # Set before dispatch so Meta retries (arriving before task runs) are dropped.
        idem_key = f"wa:processed:{msg_id}"
        if not cache.add(idem_key, True, 60 * 60 * 48):
            logger.info("Duplicate WA message %s — skipping", msg_id)
            return

        from .tasks import process_incoming_whatsapp_task
        process_incoming_whatsapp_task.delay(message, phone_number_id)


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
                    request.user.agent_profile.assigned_leads
                    .values_list('user__phone', flat=True)
                )
                qs = qs.filter(phone__in=agent_lead_phones)
            except Exception:
                qs = qs.none()
        elif role == 'developer':
            try:
                from apps.leads.models import Lead
                org = request.user.owned_organization
                org_lead_phones = Lead.objects.filter(
                    organization=org
                ).values_list('user__phone', flat=True)
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
                    request.user.agent_profile.assigned_leads
                    .values_list('user__phone', flat=True)
                )
                if session.phone not in agent_lead_phones:
                    return Response({'detail': 'Not authorized.'}, status=403)
            except Exception:
                return Response({'detail': 'Not authorized.'}, status=403)
        elif role == 'developer':
            try:
                from apps.leads.models import Lead
                org = request.user.owned_organization
                org_phones = list(
                    Lead.objects.filter(organization=org)
                    .values_list('user__phone', flat=True)
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


# ── Organization WhatsApp Config API ──────────────────────────────────────────

def _get_org_for_wa(request):
    """Return (org, error_response). Matches the OrgPaymentSettingsView pattern."""
    if request.user.role not in ('developer', 'admin'):
        return None, Response({'detail': 'Forbidden.'}, status=403)
    from apps.core.permissions import get_user_org
    org = get_user_org(request.user)
    if org is None:
        return None, Response({'detail': 'No organization linked to this account.'}, status=404)
    return org, None


class OrgWhatsAppConfigView(APIView):
    """
    GET  /whatsapp/config/  — fetch org's WA config (secrets masked as ••••••••)
    PATCH /whatsapp/config/ — partial update; sending ••••••••  leaves field unchanged
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, err = _get_org_for_wa(request)
        if err:
            return err
        config, _ = OrgWhatsAppConfig.objects.get_or_create(organization=org)
        return Response(OrgWhatsAppConfigSerializer(config).data)

    def patch(self, request):
        org, err = _get_org_for_wa(request)
        if err:
            return err
        config, _ = OrgWhatsAppConfig.objects.get_or_create(organization=org)
        serializer = OrgWhatsAppConfigSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        config.refresh_from_db()

        if config.access_token and config.phone_number_id:
            from .tasks import push_wa_business_profile_to_meta
            push_wa_business_profile_to_meta.delay(str(org.pk))

        return Response(OrgWhatsAppConfigSerializer(config).data)


class OrgWhatsAppVerifyView(APIView):
    """
    POST /whatsapp/config/verify/
    Calls Meta Graph API with org credentials to confirm they are valid.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        org, err = _get_org_for_wa(request)
        if err:
            return err
        try:
            config = org.whatsapp_config
        except OrgWhatsAppConfig.DoesNotExist:
            return Response({'ok': False, 'detail': 'No WhatsApp config set up yet.'}, status=400)

        if not config.access_token or not config.phone_number_id:
            return Response(
                {'ok': False, 'detail': 'Access token and Phone Number ID are required before verifying.'},
                status=400,
            )
        try:
            r = _http_requests.get(
                f"https://graph.facebook.com/v20.0/{config.phone_number_id}",
                headers={'Authorization': f'Bearer {config.access_token}'},
                timeout=10,
            )
            if r.status_code == 200:
                return Response({'ok': True, 'detail': 'Connection verified successfully.'})
            return Response(
                {'ok': False, 'detail': f'Meta API returned HTTP {r.status_code}. Check your credentials.'},
                status=400,
            )
        except _http_requests.exceptions.Timeout:
            return Response({'ok': False, 'detail': 'Connection timed out reaching Meta API.'}, status=502)
        except Exception as exc:
            return Response({'ok': False, 'detail': str(exc)}, status=502)


class OrgWhatsAppTestMessageView(APIView):
    """
    POST /whatsapp/config/test-message/
    Sends a test WhatsApp message to the requesting admin's phone number.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        org, err = _get_org_for_wa(request)
        if err:
            return err
        phone = request.user.phone
        if not phone:
            return Response({'ok': False, 'detail': 'Your account has no phone number set.'}, status=400)
        try:
            from .client import get_wa_client
            client = get_wa_client(org)
            client.send_text(
                phone,
                '✅ Test message from RealTron AI. Your WhatsApp integration is working correctly.',
                skip_window_check=True,
            )
            return Response({'ok': True})
        except Exception as exc:
            return Response({'ok': False, 'detail': str(exc)}, status=502)


class WaProfileSyncView(APIView):
    """
    POST /whatsapp/config/sync/
    Enqueues the Celery task that pushes business profile fields to Meta.
    Developer or admin only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        org, err = _get_org_for_wa(request)
        if err:
            return err

        try:
            cfg = OrgWhatsAppConfig.objects.get(organization=org)
        except OrgWhatsAppConfig.DoesNotExist:
            return Response({'detail': 'WhatsApp config not found.'}, status=404)

        if not cfg.access_token or not cfg.phone_number_id:
            return Response(
                {'detail': 'access_token and phone_number_id must be configured before syncing.'},
                status=400,
            )

        from .tasks import push_wa_business_profile_to_meta
        push_wa_business_profile_to_meta.delay(str(org.pk))

        return Response({
            'status':    'queued',
            'synced_at': cfg.meta_profile_synced_at,
        })


class TakeControlView(APIView):
    permission_classes = [IsAuthenticated, IsAgentOrAdmin]

    def post(self, request, session_id):
        from apps.leads.models import Lead, LeadActivity
        session = get_object_or_404(WhatsAppSession, id=session_id)
        user_org = get_user_org(request.user)

        if session.organization_id != (user_org.id if user_org else None):
            return Response({"detail": "Not found."}, status=404)

        lock_key = f"wa:agent_lock:{session_id}"
        acquired = cache.add(lock_key, str(request.user.id), timeout=180)

        if not acquired:
            held_by = cache.get(lock_key)
            return Response(
                {"detail": "Session is held by another agent.", "held_by": held_by},
                status=409,
            )

        session.conversation_mode = WhatsAppSession.ConversationMode.AGENT_MANAGED
        session.save(update_fields=["conversation_mode"])

        lead = Lead.objects.filter(user=session.user, organization=user_org).first()
        if lead:
            if request.user.role == "agent":
                try:
                    lead.assigned_agent = request.user.agent_profile
                    lead.save(update_fields=["assigned_agent"])
                except Exception:
                    pass
            LeadActivity.objects.create(
                lead=lead,
                actor=request.user,
                action=LeadActivity.ActionType.HANDOVER,
                meta={"from": "AI", "to": str(request.user.id)},
            )

        self._broadcast(session, user_org, {
            "event":      "session_taken",
            "session_id": str(session.id),
            "agent_id":   str(request.user.id),
            "agent_name": getattr(request.user, 'name', None) or str(request.user.phone),
        })

        return Response({"conversation_mode": "AGENT_MANAGED", "lock_ttl": 180})

    def _broadcast(self, session, org, payload):
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        payload["type"] = "agent_message"
        try:
            async_to_sync(channel_layer.group_send)(
                f"org_{org.id}_agent_room", payload
            )
        except Exception:
            logger.warning("TakeControlView broadcast failed", exc_info=True)


class ReleaseControlView(APIView):
    permission_classes = [IsAuthenticated, IsAgentOrAdmin]

    def post(self, request, session_id):
        from apps.leads.models import Lead, LeadActivity
        session = get_object_or_404(WhatsAppSession, id=session_id)
        user_org = get_user_org(request.user)

        if session.organization_id != (user_org.id if user_org else None):
            return Response({"detail": "Not found."}, status=404)

        lock_key = f"wa:agent_lock:{session_id}"
        holder = cache.get(lock_key)

        if request.user.role not in ("developer", "admin"):
            if str(holder) != str(request.user.id):
                return Response(
                    {"detail": "You do not hold the lock for this session."},
                    status=403,
                )

        cache.delete(lock_key)
        session.conversation_mode = WhatsAppSession.ConversationMode.AI_MANAGED
        session.save(update_fields=["conversation_mode"])

        lead = Lead.objects.filter(user=session.user, organization=user_org).first()
        if lead:
            LeadActivity.objects.create(
                lead=lead,
                actor=request.user,
                action=LeadActivity.ActionType.HANDOVER,
                meta={"from": str(request.user.id), "to": "AI"},
            )

        self._broadcast(session, user_org, {
            "event":       "session_released",
            "session_id":  str(session.id),
            "released_by": str(request.user.id),
        })

        return Response({"conversation_mode": "AI_MANAGED"})

    def _broadcast(self, session, org, payload):
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        payload["type"] = "agent_message"
        try:
            async_to_sync(channel_layer.group_send)(
                f"org_{org.id}_agent_room", payload
            )
        except Exception:
            logger.warning("ReleaseControlView broadcast failed", exc_info=True)