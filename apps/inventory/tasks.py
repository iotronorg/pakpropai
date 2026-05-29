import json
import logging

import requests
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [60, 120, 240, 480, 960]


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=5, default_retry_delay=60)
def sync_external_platform(self, connection_id: str):
    from apps.inventory.models import ExternalPlatformConnection
    from apps.inventory.sync_engine import InventorySyncOrchestrator

    try:
        connection = ExternalPlatformConnection.objects.select_related('org').get(id=connection_id)
    except ExternalPlatformConnection.DoesNotExist:
        logger.error('sync_external_platform: connection %s not found', connection_id)
        return

    connection.sync_status = 'syncing'
    connection.save(update_fields=['sync_status'])

    try:
        result = InventorySyncOrchestrator().run_sync(connection)
        connection.last_synced_at = timezone.now()
        connection.sync_status = 'idle'
        connection.error_detail = ''
        connection.save(update_fields=['last_synced_at', 'sync_status', 'error_detail'])
        logger.info(
            'sync_external_platform: %s synced=%d skipped=%d failed=%d',
            connection.platform, result.synced, result.skipped, result.failed,
        )
    except Exception as exc:
        countdown = _RETRY_DELAYS[min(self.request.retries, len(_RETRY_DELAYS) - 1)]
        if self.request.retries >= self.max_retries:
            connection.sync_status = 'error'
            connection.error_detail = str(exc)[:500]
            connection.save(update_fields=['sync_status', 'error_detail'])
            logger.error('sync_external_platform: final failure for %s: %s', connection_id, exc)
            return
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def dispatch_delta_to_platform(self, connection_id: str, property_id: str, delta_hash: str, delta: dict):
    from django.core.cache import cache
    from apps.inventory.models import ExternalPlatformConnection, WebhookDeliveryRecord

    idempotency_key = f'delta_dispatched:{connection_id}:{delta_hash}'
    if cache.get(idempotency_key):
        logger.info('dispatch_delta_to_platform: duplicate delta %s — skipped', delta_hash)
        return

    try:
        connection = ExternalPlatformConnection.objects.get(id=connection_id)
    except ExternalPlatformConnection.DoesNotExist:
        return

    if not connection.base_url:
        logger.warning('dispatch_delta_to_platform: no base_url for %s', connection_id)
        return

    record = WebhookDeliveryRecord.objects.filter(
        connection=connection, payload=delta
    ).order_by('-created_at').first()

    if not record:
        record = WebhookDeliveryRecord.objects.create(
            org=connection.org,
            connection=connection,
            event_type='property.update',
            payload=delta,
            status='pending',
        )

    record.attempt_count += 1
    try:
        signature = _sign_payload(json.dumps(delta, sort_keys=True).encode(), connection.api_secret)
        resp = requests.post(
            f'{connection.base_url}/webhook',
            json={'property_id': property_id, 'delta': delta},
            headers={'X-Signature': f'sha256={signature}', 'X-Api-Key': connection.api_key},
            timeout=15,
        )
        resp.raise_for_status()
        record.status = 'delivered'
        record.delivered_at = timezone.now()
        record.error_detail = ''
        record.save(update_fields=['status', 'delivered_at', 'error_detail', 'attempt_count'])
        cache.set(idempotency_key, '1', 86400)
    except Exception as exc:
        countdown = _RETRY_DELAYS[min(self.request.retries, len(_RETRY_DELAYS) - 1)]
        record.error_detail = str(exc)[:500]
        if self.request.retries >= self.max_retries:
            record.status = 'failed'
        record.save(update_fields=['status', 'error_detail', 'attempt_count'])
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=countdown)


@shared_task
def sync_all_active_connections():
    from apps.inventory.models import ExternalPlatformConnection
    connections = ExternalPlatformConnection.objects.filter(
        is_active=True,
        org__is_active=True,
    ).values_list('id', flat=True)
    for conn_id in connections:
        sync_external_platform.delay(str(conn_id))
    logger.info('sync_all_active_connections: dispatched %d tasks', len(connections))


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    import hmac as _hmac
    import hashlib
    if not secret:
        return ''
    return _hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
