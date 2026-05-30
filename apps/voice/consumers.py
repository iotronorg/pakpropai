import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer, AsyncJsonWebsocketConsumer
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class VoiceStreamConsumer(AsyncWebsocketConsumer):
    """
    Receives raw binary μ-law audio frames from Twilio Media Streams.
    One consumer per active call, keyed by call_sid.
    URL: ws/voice/stream/<call_sid>/
    """

    async def connect(self):
        self.call_sid = self.scope['url_route']['kwargs']['call_sid']
        self.group    = f'voice_stream_{self.call_sid}'

        session = await self._get_session()
        if session is None:
            await self.close(code=4004)
            return

        self.org_id = str(session.organization_id) if session.organization_id else None
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send(text_data=json.dumps({'event': 'stream_connected', 'call_sid': self.call_sid}))
        logger.info("VoiceStreamConsumer connected call_sid=%s", self.call_sid)

    async def receive(self, text_data=None, bytes_data=None):
        """Twilio sends base64-encoded audio in JSON frames; raw binary on binary frames."""
        if bytes_data:
            await sync_to_async(self._process_chunk)(bytes_data)
        elif text_data:
            try:
                msg = json.loads(text_data)
                if msg.get('event') == 'media':
                    import base64
                    audio = base64.b64decode(msg['media']['payload'])
                    await sync_to_async(self._process_chunk)(audio)
                elif msg.get('event') == 'stop':
                    await self._finalize()
            except Exception as exc:
                logger.debug("VoiceStreamConsumer.receive parse error: %s", exc)

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)
        await self._finalize()
        logger.info("VoiceStreamConsumer disconnected call_sid=%s code=%s", self.call_sid, code)

    def _process_chunk(self, audio_bytes: bytes) -> None:
        from apps.voice.ai_processor import AIVoiceProcessor
        AIVoiceProcessor.process_audio_chunk(self.call_sid, audio_bytes)

    async def _finalize(self):
        from apps.voice.models import VoiceCallSession
        from django.utils import timezone
        try:
            session = await sync_to_async(
                VoiceCallSession.objects.filter(call_sid=self.call_sid).first
            )()
            if session and session.status not in (
                VoiceCallSession.Status.COMPLETED,
                VoiceCallSession.Status.FAILED,
            ):
                await sync_to_async(VoiceCallSession.objects.filter(
                    call_sid=self.call_sid
                ).update)(
                    status=VoiceCallSession.Status.COMPLETED,
                    ended_at=timezone.now(),
                )
        except Exception as exc:
            logger.debug("VoiceStreamConsumer._finalize error: %s", exc)

    @sync_to_async
    def _get_session(self):
        from apps.voice.models import VoiceCallSession
        return VoiceCallSession.objects.filter(call_sid=self.call_sid).select_related(
            'organization'
        ).first()


class VoiceAgentRoomConsumer(AsyncJsonWebsocketConsumer):
    """
    Read-only JSON consumer for the agent dashboard panel.
    Receives: transcription | call_status | barge_in events.
    URL: ws/voice/room/<org_id>/
    Auth: JWTAuthMiddleware (same as AgentRoomConsumer).
    """

    async def connect(self):
        self.org_id = self.scope['url_route']['kwargs']['org_id']
        user        = self.scope.get('user')

        if user is None or not user.is_authenticated:
            await self.close(code=4001)
            return

        if not await self._user_in_org(user, self.org_id):
            await self.close(code=4003)
            return

        self.group = f'voice_room_{self.org_id}'
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group, self.channel_name)

    # ── Channel layer event handlers ──────────────────────────────────────────

    async def transcription(self, event):
        await self.send_json({
            'event':     'transcription',
            'call_sid':  event['call_sid'],
            'text':      event['text'],
            'role':      event.get('role', 'ai'),
            'timestamp': event.get('timestamp', ''),
        })

    async def call_status(self, event):
        await self.send_json({
            'event':    'call_status',
            'call_sid': event['call_sid'],
            'status':   event['status'],
            'duration': event.get('duration', 0),
        })

    async def barge_in(self, event):
        await self.send_json({
            'event':      'barge_in',
            'call_sid':   event['call_sid'],
            'agent_name': event.get('agent_name', ''),
            'timestamp':  event.get('timestamp', ''),
        })

    @sync_to_async
    def _user_in_org(self, user, org_id: str) -> bool:
        from apps.core.permissions import get_user_org
        if user.role == 'admin':
            return True
        org = get_user_org(user)
        return org is not None and str(org.id) == org_id
