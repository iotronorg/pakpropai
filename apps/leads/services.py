"""
Lead assignment entry points. The matching itself lives in ``apps.leads.assignment``
(T-3040, ADR-063): the organization's rules (strategy, city/area/language/
specialization matches, caps, paused agents) decide who may get a lead and in
which order. Without rules the order is the old score:

  rating * 4, +6 featured, +4 verified, +2 primary city, -0.5 per open lead
"""
import logging

from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)


def suggest_agents_for_lead(lead, limit: int = 3, rules=None) -> list:
    """
    Up to ``limit`` ranked agents for this lead under its organization's rules;
    each carries ``agent.assignment`` (strategy, matched rules, open leads, cap).
    Never raises: returns [] on any error.
    """
    from .assignment import rank
    try:
        return [c.agent for c in rank(lead, rules)[:limit]]
    except Exception as exc:
        logger.error(f"suggest_agents_for_lead failed for lead {getattr(lead, 'id', None)}: {exc}")
        return []


def assign_agent_to_lead(lead, agent, actor=None, auto: bool = False, reason: dict | None = None,
                         notify: bool = True) -> None:
    """Assign agent to lead, mark its routing as assigned (unless closed) and log the activity."""
    from apps.agents.models import Agent
    from apps.leads.models import Lead, LeadActivity

    old_agent = lead.assigned_agent
    lead.assigned_agent = agent
    if agent.organization_id and not lead.organization_id:
        lead.organization_id = agent.organization_id
    fields = ['assigned_agent', 'organization']
    if lead.routing_state != Lead.RoutingState.CLOSED:
        lead.routing_state = Lead.RoutingState.AGENT_ASSIGNED
        fields.append('routing_state')
    lead.save(update_fields=fields)
    Agent.objects.filter(pk=agent.pk).update(total_leads=F('total_leads') + 1, last_active_at=timezone.now())

    LeadActivity.objects.create(
        lead=lead,
        actor=actor,
        action=LeadActivity.ActionType.ASSIGNED,
        notes=f"Auto-assigned to {agent.name}" if auto else f"Assigned to {agent.name}",
        meta={
            'old_agent_id': old_agent.id if old_agent else None,
            'new_agent_id': agent.id,
            'new_agent_name': agent.name,
            'auto': auto,
            **({'rule': reason} if reason else {}),
        },
    )
    if not notify:
        return

    # Notify the newly assigned agent (dashboard bell + WhatsApp)
    try:
        if agent.user:
            from apps.notifications.services import notify_user
            from .privacy import phone_text
            client_phone = phone_text(lead)
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
