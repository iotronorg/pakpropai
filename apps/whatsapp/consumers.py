import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache

logger = logging.getLogger(__name__)


class AgentRoomConsumer(AsyncWebsocketConsumer):
    """
    Real-time agent room for one organisation.
    Group name: org_{org_id}_agent_room
    """

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self):
        user = self.scope.get("user")
        scope_org_id = self.scope.get("org_id")
        url_org_id = self.scope["url_route"]["kwargs"]["org_id"]

        # Org mismatch or unauthenticated — close before accepting
        if not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4003)
            return
        if scope_org_id != url_org_id:
            await self.close(code=4003)
            return

        # Role check — agent, developer, or platform admin only
        allowed = await self._is_allowed(user, url_org_id)
        if not allowed:
            await self.close(code=4003)
            return

        self.org_id = url_org_id
        self.group_name = f"org_{url_org_id}_agent_room"
        self.user = user

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Send list of currently AGENT_MANAGED sessions on connect
        active = await self._get_active_sessions()
        await self.send(text_data=json.dumps({
            "type": "connected",
            "active_sessions": active,
        }))

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "ping":
            session_id = data.get("session_id")
            if session_id:
                lock_key = f"wa:agent_lock:{session_id}"
                holder = cache.get(lock_key)
                if str(holder) == str(self.user.id):
                    cache.set(lock_key, str(self.user.id), timeout=180)
            await self.send(text_data=json.dumps({"type": "pong"}))

        elif msg_type == "send_message":
            await self._handle_send(data)

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        # Redis lock drains via TTL — Celery revert task handles DB cleanup

    # ── Group message handler (called by channel_layer.group_send) ─────────────

    async def agent_message(self, event):
        await self.send(text_data=json.dumps(event))

    # ── Private helpers ────────────────────────────────────────────────────────

    @sync_to_async
    def _is_allowed(self, user, org_id):
        # Platform admin can join any room
        if user.role == "admin":
            return True
        # Agent or developer must be an active member of this org
        from apps.organizations.models import OrganizationMembership
        return OrganizationMembership.objects.filter(
            user=user,
            organization_id=org_id,
            is_active=True,
        ).exists()

    @sync_to_async
    def _get_active_sessions(self):
        from apps.whatsapp.models import WhatsAppSession
        qs = WhatsAppSession.objects.filter(
            organization_id=self.org_id,
            conversation_mode="AGENT_MANAGED",
        ).values("id", "phone")
        return [{"session_id": str(s["id"]), "phone": s["phone"]} for s in qs]

    async def _handle_send(self, data):
        session_id = data.get("session_id", "")
        body = data.get("body", "").strip()
        if not session_id or not body:
            return

        lock_key = f"wa:agent_lock:{session_id}"
        holder = cache.get(lock_key)
        if str(holder) != str(self.user.id):
            await self.send(text_data=json.dumps({
                "type": "error",
                "detail": "You do not hold the lock for this session.",
            }))
            return

        await self._send_whatsapp(session_id, body)

    @sync_to_async
    def _send_whatsapp(self, session_id, body):
        from apps.whatsapp.models import WhatsAppSession, WhatsAppMessage
        from apps.whatsapp.client import get_wa_client
        try:
            session = WhatsAppSession.objects.select_related("organization").get(id=session_id)
            wa_client = get_wa_client(session.organization)
            response = wa_client.send_text(session.phone, body)
            wa_msg_id = response.get("messages", [{}])[0].get("id", "")
            WhatsAppMessage.objects.create(
                session=session,
                wa_message_id=wa_msg_id or f"agent-{session_id[:8]}",
                direction="outbound",
                msg_type="text",
                body=body,
                raw_payload=response,
            )
        except Exception:
            logger.exception("AgentRoomConsumer._send_whatsapp failed")
