import logging
import time

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0, ignore_result=True, queue='high-resource')
def transcribe_and_respond(self, call_sid: str) -> None:
    """
    Pull buffered audio, run STT, generate AI reply, speak it back via Twilio <Say>.
    max_retries=0 — voice is real-time; stale retries are harmful.
    Budget: < 800ms total.
    """
    start = time.perf_counter()

    from apps.voice.models import VoiceCallSession
    from apps.voice.ai_processor import CallAudioBuffer

    try:
        session = VoiceCallSession.objects.select_related('organization', 'lead').get(
            call_sid=call_sid
        )
    except VoiceCallSession.DoesNotExist:
        logger.warning("transcribe_and_respond: no session for call_sid=%s", call_sid)
        return

    if session.status not in (VoiceCallSession.Status.AI_HANDLING, VoiceCallSession.Status.RINGING):
        return  # agent has taken over or call ended — don't speak AI reply

    # 1. Flush audio buffer
    buf   = CallAudioBuffer(call_sid)
    audio = buf.flush()
    if not audio:
        return

    # 2. STT: μ-law 8kHz → text
    try:
        from apps.whatsapp.stt_services import STTService
        result = STTService.transcribe(audio, mime_type='audio/ulaw')
        text   = result.text.strip()
    except Exception as exc:
        logger.warning("STT failed for call_sid=%s: %s", call_sid, exc)
        return

    if not text:
        return

    # 3. Append caller transcript line
    _append_transcript(session, f'[CALLER] {text}')

    # 4. Broadcast transcription to agent panel
    _broadcast_transcription(session, text, role='caller')

    # 5. Generate AI reply (voice-optimised — short)
    try:
        from apps.ai.service import AIServiceManager
        phone         = session.from_phone
        org           = session.organization
        voice_message = f"[VOICE MODE — reply ≤ 20 words] {text}"
        reply = AIServiceManager.process(phone=phone, message=voice_message, organization=org)
    except Exception as exc:
        logger.warning("AI reply failed for call_sid=%s: %s", call_sid, exc)
        return

    if not reply:
        return

    # 6. Speak reply via Twilio <Say>
    try:
        from apps.voice.providers.base import get_org_provider
        provider = get_org_provider(org)
        voice    = getattr(org.voice_config, 'ai_voice_name', 'Polly.Joanna') if hasattr(org, 'voice_config') else 'Polly.Joanna'
        provider.inject_voice_reply(call_sid, reply, voice=voice)
    except Exception as exc:
        logger.warning("inject_voice_reply failed call_sid=%s: %s", call_sid, exc)

    # 7. Append AI transcript line + broadcast
    _append_transcript(session, f'[AI] {reply}')
    _broadcast_transcription(session, reply, role='ai')

    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms > 800:
        logger.warning("transcribe_and_respond exceeded 800ms budget: %.0fms call_sid=%s", elapsed_ms, call_sid)

    # Update status if still ringing
    if session.status == VoiceCallSession.Status.RINGING:
        VoiceCallSession.objects.filter(call_sid=call_sid).update(
            status=VoiceCallSession.Status.AI_HANDLING
        )


@shared_task(ignore_result=True)
def broadcast_call_status(call_sid: str, status: str, org_id: str, duration: int = 0) -> None:
    """Publish a call status update to the org's voice room channel group."""
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            f'voice_room_{org_id}',
            {
                'type':     'call_status',
                'event':    'call_status',
                'call_sid': call_sid,
                'status':   status,
                'duration': duration,
            }
        )
    except Exception as exc:
        logger.warning("broadcast_call_status failed: %s", exc)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _append_transcript(session, line: str) -> None:
    from apps.voice.models import VoiceCallSession
    from django.db.models import Value
    from django.db.models.functions import Concat
    VoiceCallSession.objects.filter(call_sid=session.call_sid).update(
        transcript=Concat('transcript', Value(f'\n{line}'))
    )


def _broadcast_transcription(session, text: str, role: str) -> None:
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    if not session.organization_id:
        return
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            f'voice_room_{session.organization_id}',
            {
                'type':      'transcription',
                'event':     'transcription',
                'call_sid':  session.call_sid,
                'text':      text,
                'role':      role,
                'timestamp': timezone.now().isoformat(),
            }
        )
    except Exception as exc:
        logger.debug("_broadcast_transcription failed: %s", exc)
