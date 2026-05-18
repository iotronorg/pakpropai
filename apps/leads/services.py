"""
Lead auto-assignment — matches a lead to the best available agent.

Scoring formula (higher = better):
  base  = rating * 4
  bonus = +6 if is_featured
         +4 if is_verified
         +2 if city is primary_city
  load  = -0.5 per assigned lead (prevents pile-up on popular agents)
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Intent → expected agent specialization mapping
_INTENT_SPEC_MAP = {
    'buy':    {'residential_buy', 'plots', 'new_projects', 'luxury'},
    'sell':   {'residential_buy', 'plots', 'new_projects', 'luxury'},
    'rent':   {'residential_rent'},
    'invest': {'new_projects', 'plots', 'luxury', 'commercial'},
    'loan':   {'residential_buy'},
    'tax':    set(),  # any agent
}


def suggest_agents_for_lead(lead, limit: int = 3) -> list:
    """
    Return up to `limit` ranked Agent objects suited for this lead.
    Never raises — returns [] on any error.
    """
    from apps.agents.models import Agent
    from apps.leads.models import Lead

    try:
        candidates = Agent.objects.filter(is_active=True).prefetch_related()

        # City filter: agent.cities JSON array contains lead.city_interest
        city = (lead.city_interest or '').strip().lower()
        if city:
            candidates = candidates.filter(cities__icontains=city)

        # Specialization filter: if intent maps to a known spec set, filter loosely
        specs = _INTENT_SPEC_MAP.get(lead.intent or '', set())
        if specs:
            # Keep agents with ANY matching specialization (JSON overlap check)
            spec_filtered = [
                a for a in candidates
                if not specs or any(s in (a.specializations or []) for s in specs)
            ]
            # Fall back to all city-matched candidates if no spec match
            candidates = spec_filtered if spec_filtered else list(candidates)
        else:
            candidates = list(candidates)

        # Load count per agent
        agent_ids = [a.id for a in candidates]
        from django.db.models import Count
        load_map = dict(
            Lead.objects.filter(assigned_agent_id__in=agent_ids)
                        .values_list('assigned_agent_id')
                        .annotate(c=Count('id'))
                        .values_list('assigned_agent_id', 'c')
        )

        # Score each candidate
        def _score(agent) -> float:
            s  = float(agent.rating) * 4
            s += 6 if agent.is_featured  else 0
            s += 4 if agent.is_verified  else 0
            s += 2 if agent.primary_city.lower() == city else 0
            s -= load_map.get(agent.id, 0) * 0.5
            return s

        ranked = sorted(candidates, key=_score, reverse=True)
        return ranked[:limit]

    except Exception as exc:
        logger.error(f"suggest_agents_for_lead failed for lead {lead.id}: {exc}")
        return []


def assign_agent_to_lead(lead, agent, actor=None) -> None:
    """Assign agent to lead and log the activity."""
    from apps.leads.models import LeadActivity

    old_agent = lead.assigned_agent
    lead.assigned_agent = agent
    if agent.organization_id and not lead.organization_id:
        lead.organization_id = agent.organization_id
    lead.save(update_fields=['assigned_agent', 'organization'])

    LeadActivity.objects.create(
        lead=lead,
        actor=actor,
        action=LeadActivity.ActionType.ASSIGNED,
        notes=f"Assigned to {agent.name}",
        meta={
            'old_agent_id': old_agent.id if old_agent else None,
            'new_agent_id': agent.id,
            'new_agent_name': agent.name,
        },
    )

    # Notify the newly assigned agent (dashboard bell + WhatsApp)
    try:
        if agent.user:
            from apps.notifications.services import notify_user
            client_phone = lead.user.phone
            notify_user(
                agent.user,
                title="New Lead Assigned",
                message=(
                    f"📋 *New Lead Assigned*\n\n"
                    f"You have been assigned a new lead:\n"
                    f"📱 Client: {client_phone}\n"
                    f"📍 City: {lead.city_interest or 'Not specified'}\n"
                    f"🎯 Intent: {lead.get_intent_display() if lead.intent else 'Not specified'}\n\n"
                    "Check your dashboard for full details."
                ),
            )
    except Exception as exc:
        logger.warning(f"Failed to notify agent {agent.id} of assignment: {exc}")
