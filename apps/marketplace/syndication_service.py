import logging

from django.core.exceptions import PermissionDenied

from .models import BrokerNetworkPartnership, SyndicationListing

logger = logging.getLogger(__name__)


class SyndicationVisibilityLayer:

    def get_visible_listings(self, requesting_org, requesting_agent=None):
        """Return SYNDICATED listings visible to requesting_org/agent."""
        syndicated = SyndicationListing.objects.filter(
            status=SyndicationListing.Status.SYNDICATED
        ).select_related('developer_org', 'property')

        platform_wide = syndicated.filter(
            syndication_scope=SyndicationListing.SyndicationScope.PLATFORM_WIDE
        )

        active_developer_orgs = BrokerNetworkPartnership.objects.filter(
            status=BrokerNetworkPartnership.Status.ACTIVE,
        ).filter(
            _broker_filter(requesting_org, requesting_agent)
        ).values_list('developer_org_id', flat=True)

        selected_partner = syndicated.filter(
            syndication_scope=SyndicationListing.SyndicationScope.SELECTED_PARTNERS,
            developer_org_id__in=active_developer_orgs,
        )

        from django.db.models import QuerySet
        combined_ids = (
            list(platform_wide.values_list('pk', flat=True)) +
            list(selected_partner.values_list('pk', flat=True))
        )
        return SyndicationListing.objects.filter(pk__in=combined_ids).select_related(
            'developer_org', 'property'
        )

    def is_listing_visible(self, listing, requesting_org, requesting_agent=None):
        return self.get_visible_listings(requesting_org, requesting_agent).filter(
            pk=listing.pk
        ).exists()

    def syndicate_listing(self, listing, developer_org):
        if listing.developer_org_id != developer_org.pk:
            raise PermissionDenied('Only the developer org that owns this listing can syndicate it.')
        listing.status = SyndicationListing.Status.SYNDICATED
        listing.save(update_fields=['status', 'updated_at'])
        return listing

    def withdraw_listing(self, listing, developer_org):
        if listing.developer_org_id != developer_org.pk:
            raise PermissionDenied('Only the developer org that owns this listing can withdraw it.')
        listing.status = SyndicationListing.Status.WITHDRAWN
        listing.save(update_fields=['status', 'updated_at'])
        return listing


def _broker_filter(requesting_org, requesting_agent):
    """Build Q filter matching either broker_org or broker_agent."""
    from django.db.models import Q
    q = Q()
    if requesting_org is not None:
        q |= Q(broker_org=requesting_org)
    if requesting_agent is not None:
        q |= Q(broker_agent=requesting_agent)
    return q
