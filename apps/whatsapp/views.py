import hashlib
import hmac
import json
import logging
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

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