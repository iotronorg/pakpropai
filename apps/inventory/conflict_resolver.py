import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Resolution:
    action: str
    alert: Optional[object] = None


class InventorySyncConflictResolver:

    def resolve(self, connection, property_id: str, external_delta: dict, internal_state: dict) -> Resolution:
        from apps.inventory.models import SyncConflictAlert
        from apps.properties.models import Property
        from apps.notifications.models import Notification

        try:
            prop = Property.objects.get(id=property_id)
        except Property.DoesNotExist:
            return Resolution(action='skip')

        mode = connection.conflict_resolution

        if mode == 'internal_wins':
            alert = SyncConflictAlert.objects.create(
                org=connection.org,
                connection=connection,
                property=prop,
                external_delta=external_delta,
                internal_state=internal_state,
                resolution='internal_wins',
            )
            self._notify_admin(connection, prop, 'internal_wins')
            return Resolution(action='internal_wins', alert=alert)

        elif mode == 'manual':
            alert = SyncConflictAlert.objects.create(
                org=connection.org,
                connection=connection,
                property=prop,
                external_delta=external_delta,
                internal_state=internal_state,
                resolution='pending',
            )
            self._notify_admin(connection, prop, 'manual')
            return Resolution(action='manual', alert=alert)

        elif mode == 'external_wins':
            # Only apply if no active lock
            from apps.escrow.models import EscrowDeal
            if EscrowDeal.objects.filter(property_id=property_id, status='locked').exists():
                alert = SyncConflictAlert.objects.create(
                    org=connection.org,
                    connection=connection,
                    property=prop,
                    external_delta=external_delta,
                    internal_state=internal_state,
                    resolution='internal_wins',
                )
                self._notify_admin(connection, prop, 'internal_wins')
                return Resolution(action='internal_wins', alert=alert)
            return Resolution(action='external_wins')

        return Resolution(action='skip')

    @staticmethod
    def _notify_admin(connection, prop, resolution_type: str) -> None:
        try:
            from apps.notifications.models import Notification
            admin = connection.org.admin_user
            if not admin:
                return
            Notification.objects.create(
                user=admin,
                title='Inventory Sync Conflict',
                message=f'Sync conflict ({resolution_type}): external update blocked by active deal lock on "{prop.title}"',
            )
        except Exception as exc:
            logger.warning('InventorySyncConflictResolver._notify_admin failed: %s', exc)
