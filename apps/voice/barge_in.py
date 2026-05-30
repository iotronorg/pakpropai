"""
BargeInLatencyManager — executes agent barge-in with Redis NX lock,
< 100ms target from REST call to Twilio stream redirect.
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)

_LOCK_KEY = 'barge_in_lock:{call_sid}'
_LOCK_TTL = 10  # seconds


@dataclass
class BargeInResult:
    success:    bool
    latency_ms: float
    error:      Optional[str] = None


class BargeInLatencyManager:

    @staticmethod
    def execute_barge_in(call_sid: str, agent_user, org) -> BargeInResult:
        start    = time.perf_counter()
        lock_key = _LOCK_KEY.format(call_sid=call_sid)

        # 1. Acquire Redis NX lock — prevents double barge-in
        acquired = cache.add(lock_key, '1', timeout=_LOCK_TTL)
        if not acquired:
            return BargeInResult(
                success=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error='barge_in_already_in_progress',
            )

        try:
            from apps.voice.models import VoiceCallSession
            from django.db import transaction
            from django.utils import timezone

            # 2. Verify call status in atomic block
            with transaction.atomic():
                try:
                    session = VoiceCallSession.objects.select_for_update().get(call_sid=call_sid)
                except VoiceCallSession.DoesNotExist:
                    return BargeInResult(
                        success=False,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        error='call_not_found',
                    )

                if session.status != VoiceCallSession.Status.AI_HANDLING:
                    return BargeInResult(
                        success=False,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        error=f'invalid_status:{session.status}',
                    )

                # 3. Call Twilio redirect
                from apps.voice.providers.base import get_org_provider
                provider = get_org_provider(org)
                ok = provider.redirect_to_agent(call_sid)

                if not ok:
                    return BargeInResult(
                        success=False,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        error='twilio_redirect_failed',
                    )

                # 4. Update session
                now = timezone.now()
                session.status         = VoiceCallSession.Status.AGENT_JOINED
                session.barge_in_at    = now
                session.barge_in_agent = agent_user
                session.save(update_fields=['status', 'barge_in_at', 'barge_in_agent'])

            latency_ms = (time.perf_counter() - start) * 1000

            # 5. Broadcast barge-in event to agent panel
            _broadcast_barge_in(session, agent_user, org)

            logger.info("Barge-in complete call_sid=%s agent=%s latency=%.0fms",
                        call_sid, agent_user.phone, latency_ms)
            return BargeInResult(success=True, latency_ms=latency_ms)

        finally:
            cache.delete(lock_key)


def _broadcast_barge_in(session, agent_user, org) -> None:
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    from django.utils import timezone

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            f'voice_room_{org.id}',
            {
                'type':       'barge_in',
                'call_sid':   session.call_sid,
                'agent_name': agent_user.name or agent_user.phone,
                'timestamp':  timezone.now().isoformat(),
            }
        )
    except Exception as exc:
        logger.debug("_broadcast_barge_in failed: %s", exc)
