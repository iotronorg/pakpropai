import hashlib
import hmac
import json
import logging

from django.core.exceptions import PermissionDenied
from django.utils import timezone

logger = logging.getLogger(__name__)

_IDEMPOTENCY_TTL = 86400  # 24 hours


def _verify_hmac(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    if not secret:
        return True  # skip verification if no secret configured
    expected = hmac.new(
        secret.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    # Accept both 'sha256=<hex>' and plain '<hex>' formats
    supplied = signature_header.split('=', 1)[-1] if '=' in signature_header else signature_header
    return hmac.compare_digest(expected, supplied)


def _idempotency_key_seen(key: str) -> bool:
    from django.core.cache import cache
    cache_key = f'webhook_idempotency:{key}'
    if cache.get(cache_key):
        return True
    cache.set(cache_key, '1', _IDEMPOTENCY_TTL)
    return False


class PropertyWebhookPipeline:

    def handle_inbound(self, connection, raw_payload: bytes, signature_header: str = '') -> dict:
        if not _verify_hmac(raw_payload, signature_header, connection.api_secret):
            raise PermissionDenied('Invalid webhook signature')

        try:
            body = json.loads(raw_payload)
        except Exception:
            raise PermissionDenied('Invalid webhook payload — not valid JSON')

        idempotency_key = body.get('idempotency_key') or body.get('id') or ''
        if idempotency_key and _idempotency_key_seen(f'{connection.id}:{idempotency_key}'):
            logger.info('PropertyWebhookPipeline: duplicate idempotency key %s — skipped', idempotency_key)
            return {'status': 'duplicate', 'skipped': True}

        from apps.inventory.tasks import sync_external_platform
        sync_external_platform.delay(str(connection.id))
        return {'status': 'queued', 'connection_id': str(connection.id)}

    def dispatch_outbound(self, connection, property_id: str, delta: dict) -> None:
        import hashlib
        from apps.inventory.models import WebhookDeliveryRecord
        from apps.inventory.tasks import dispatch_delta_to_platform

        delta_hash = hashlib.sha256(
            json.dumps(delta, sort_keys=True).encode()
        ).hexdigest()[:16]

        record = WebhookDeliveryRecord.objects.create(
            org=connection.org,
            connection=connection,
            event_type='property.update',
            payload=delta,
            status='pending',
        )

        dispatch_delta_to_platform.delay(
            str(connection.id), property_id, delta_hash, delta
        )

        return record
