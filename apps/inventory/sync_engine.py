import hashlib
import json
import logging

from django.db import transaction

from apps.inventory.adapters.base import SyncResult

logger = logging.getLogger(__name__)


def _adapter_for(connection):
    from apps.inventory.adapters.base import AdapterConfig
    from apps.inventory.adapters.zameen import ZameenAdapter
    from apps.inventory.adapters.bayut import BayutAdapter
    from apps.inventory.adapters.rightmove import RightmoveAdapter
    from apps.inventory.adapters.generic_rest import GenericRestAdapter

    cfg = AdapterConfig(
        platform=connection.platform,
        base_url=connection.base_url,
        api_key=connection.api_key,
        api_secret=connection.api_secret,
        field_mappings=connection.field_mappings or {},
    )
    return {
        'zameen':         ZameenAdapter,
        'bayut':          BayutAdapter,
        'rightmove':      RightmoveAdapter,
        'propertyfinder': GenericRestAdapter,
        'zillow':         GenericRestAdapter,
        'custom':         GenericRestAdapter,
    }.get(connection.platform, GenericRestAdapter)(cfg)


def _listing_key(mapped: dict) -> str:
    parts = [
        str(mapped.get('external_id', '')),
        str(mapped.get('title', '')),
        str(mapped.get('city', '')),
        str(mapped.get('location', '')),
    ]
    return hashlib.md5('|'.join(parts).encode()).hexdigest()


class InventorySyncOrchestrator:

    def run_sync(self, connection) -> SyncResult:
        from apps.properties.models import Property

        result = SyncResult(platform=connection.platform)
        adapter = _adapter_for(connection)

        try:
            raw_listings = adapter.poll_listings()
        except Exception as exc:
            logger.error('InventorySyncOrchestrator.run_sync: poll failed for %s: %s', connection.id, exc)
            result.failed += 1
            result.errors.append(str(exc))
            return result

        for raw in raw_listings:
            try:
                delta = adapter.map_listing(raw)
                if not delta:
                    result.skipped += 1
                    continue

                key = _listing_key(delta)
                existing = Property.objects.filter(
                    organization=connection.org,
                    title=delta.get('title', ''),
                    city=delta.get('city', ''),
                ).first()

                if existing:
                    self._apply_delta(connection, str(existing.id), delta, existing)
                    result.synced += 1
                else:
                    result.skipped += 1
            except Exception as exc:
                logger.warning('InventorySyncOrchestrator: listing sync failed: %s', exc)
                result.failed += 1
                result.errors.append(str(exc))

        return result

    def _apply_delta(self, connection, property_id: str, delta: dict, prop=None) -> None:
        from apps.properties.models import Property

        with transaction.atomic():
            try:
                locked_prop = Property.objects.select_for_update().get(id=property_id)
            except Property.DoesNotExist:
                return

            if self._check_deal_lock_conflict(property_id):
                from apps.inventory.conflict_resolver import InventorySyncConflictResolver
                internal_state = {
                    'title': locked_prop.title,
                    'price': locked_prop.price,
                    'city':  locked_prop.city,
                }
                InventorySyncConflictResolver().resolve(
                    connection=connection,
                    property_id=property_id,
                    external_delta=delta,
                    internal_state=internal_state,
                )
                return

            # Apply safe fields only
            update_fields = []
            if 'title' in delta and delta['title']:
                locked_prop.title = delta['title']
                update_fields.append('title')
            if 'price' in delta and delta['price']:
                locked_prop.price = int(delta['price'])
                update_fields.append('price')
            if 'city' in delta and delta['city']:
                locked_prop.city = delta['city']
                update_fields.append('city')
            if 'location' in delta and delta['location']:
                locked_prop.location = delta['location']
                update_fields.append('location')

            if update_fields:
                locked_prop.save(update_fields=update_fields)

            self._dispatch_delta_to_external(connection, property_id, delta)

    @staticmethod
    def _check_deal_lock_conflict(property_id: str) -> bool:
        from apps.escrow.models import EscrowDeal
        return EscrowDeal.objects.filter(
            property_id=property_id,
            status='locked',
        ).exists()

    @staticmethod
    def _dispatch_delta_to_external(connection, property_id: str, delta: dict) -> None:
        if connection.sync_direction not in ('outbound', 'bidirectional'):
            return
        try:
            from apps.inventory.tasks import dispatch_delta_to_platform
            delta_hash = hashlib.sha256(
                json.dumps(delta, sort_keys=True).encode()
            ).hexdigest()[:16]
            dispatch_delta_to_platform.delay(
                str(connection.id), property_id, delta_hash, delta
            )
        except Exception as exc:
            logger.warning('_dispatch_delta_to_external failed: %s', exc)
