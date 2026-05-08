import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def refresh_agent_performance_snapshots():
    """
    Daily task: recompute each agent's performance counters from live DB counts.
    Keeps total_leads, total_listings, and closed_deals in sync without
    requiring every write path to manually increment these fields.
    """
    from django.db.models import Count, Q
    from .models import Agent
    from apps.leads.models import Lead
    from apps.properties.models import Property
    from apps.escrow.models import EscrowDeal

    agents = Agent.objects.filter(is_active=True).only('id', 'total_leads', 'total_listings', 'closed_deals')
    updated = 0

    for agent in agents:
        lead_count = Lead.objects.filter(assigned_agent=agent).count()
        listing_count = Property.objects.filter(
            assigned_agent=agent, is_active=True
        ).count()
        closed_count = EscrowDeal.objects.filter(
            agent=agent,
            status__in=(EscrowDeal.Status.RELEASED,),
        ).count()

        changed = (
            agent.total_leads    != lead_count
            or agent.total_listings != listing_count
            or agent.closed_deals   != closed_count
        )
        if changed:
            Agent.objects.filter(pk=agent.pk).update(
                total_leads    = lead_count,
                total_listings = listing_count,
                closed_deals   = closed_count,
            )
            updated += 1

    logger.info(f"refresh_agent_performance_snapshots: updated {updated}/{agents.count()} agents")
    return updated
