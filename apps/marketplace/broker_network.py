import logging

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import BrokerNetworkPartnership, SyndicationListing
from .syndication_service import SyndicationVisibilityLayer

logger = logging.getLogger(__name__)


class BrokerNetworkService:

    def invite_partner(
        self,
        developer_org,
        broker_org=None,
        broker_agent=None,
        commission_override_type=None,
        commission_override_value=None,
        notes='',
    ):
        if broker_org is None and broker_agent is None:
            raise ValueError('Must specify either broker_org or broker_agent.')
        if broker_org is not None and broker_org.pk == developer_org.pk:
            raise ValueError('A developer org cannot partner with itself.')

        partnership = BrokerNetworkPartnership.objects.create(
            developer_org=developer_org,
            broker_org=broker_org,
            broker_agent=broker_agent,
            status=BrokerNetworkPartnership.Status.INVITED,
            commission_override_type=commission_override_type,
            commission_override_value=commission_override_value,
            notes=notes,
        )
        _notify_broker(partnership, developer_org)
        return partnership

    def accept_invitation(self, partnership, accepting_user):
        if not _user_is_broker_party(accepting_user, partnership):
            raise PermissionDenied('Only the invited broker party can accept this invitation.')
        partnership.status = BrokerNetworkPartnership.Status.ACTIVE
        partnership.activated_at = timezone.now()
        partnership.save(update_fields=['status', 'activated_at'])
        return partnership

    def revoke_partnership(self, partnership, developer_org):
        if partnership.developer_org_id != developer_org.pk:
            raise PermissionDenied('Only the developer org can revoke this partnership.')
        partnership.status = BrokerNetworkPartnership.Status.REVOKED
        partnership.save(update_fields=['status'])
        return partnership

    def suspend_partnership(self, partnership, developer_org):
        if partnership.developer_org_id != developer_org.pk:
            raise PermissionDenied('Only the developer org can suspend this partnership.')
        partnership.status = BrokerNetworkPartnership.Status.SUSPENDED
        partnership.save(update_fields=['status'])
        return partnership

    def get_active_partners(self, developer_org):
        return BrokerNetworkPartnership.objects.filter(
            developer_org=developer_org,
            status=BrokerNetworkPartnership.Status.ACTIVE,
        ).select_related('broker_org', 'broker_agent')

    def get_active_listings_for_broker(self, broker_org, broker_agent=None):
        return SyndicationVisibilityLayer().get_visible_listings(broker_org, broker_agent)


def _user_is_broker_party(user, partnership):
    if partnership.broker_agent_id and partnership.broker_agent_id == user.pk:
        return True
    if partnership.broker_org_id:
        return getattr(user, 'organization_id', None) == partnership.broker_org_id
    return False


def _notify_broker(partnership, developer_org):
    try:
        from apps.notifications.models import Notification
        recipient = None
        if partnership.broker_agent:
            recipient = partnership.broker_agent
        elif partnership.broker_org and partnership.broker_org.admin_user:
            recipient = partnership.broker_org.admin_user
        if recipient:
            Notification.objects.create(
                user=recipient,
                title='Partnership Invitation',
                message=f'Partnership invitation from {developer_org.name}.',
                channel=Notification.Channel.WHATSAPP,
            )
    except Exception:
        logger.warning('Failed to notify broker of partnership invitation', exc_info=True)
