import datetime
from django.db.models import Count
from django.db.models.functions import TruncWeek, TruncMonth
from django.utils import timezone


def compute_agent_stats(agent, period=None):
    """Return full KPI dict for one agent. Two batched queries — no N+1."""
    from apps.leads.models import Lead

    leads_qs = Lead.objects.filter(assigned_agent=agent)
    lead_ids  = list(leads_qs.values_list('id', flat=True))

    total_leads  = len(lead_ids)
    closed_leads = leads_qs.filter(routing_state=Lead.RoutingState.CLOSED).count()

    status_counts = dict(
        leads_qs.values_list('status')
                .annotate(c=Count('id'))
                .values_list('status', 'c')
    )
    source_counts = dict(
        leads_qs.values_list('source')
                .annotate(c=Count('id'))
                .values_list('source', 'c')
    )

    result = {
        'agent_id':               agent.id,
        'name':                   agent.name,
        'availability_status':    agent.availability_status,
        'total_leads':            total_leads,
        'closed_leads':           closed_leads,
        'conversion_rate':        round(closed_leads / total_leads * 100, 1) if total_leads else 0.0,
        'closed_deals':           agent.closed_deals,
        'avg_response_time_hours': _compute_response_time(lead_ids),
        'rating':                 float(agent.rating),
        'is_verified':            agent.is_verified,
        'by_status':              status_counts,
        'by_source':              source_counts,
    }

    if period in ('weekly', 'monthly'):
        result['trend'] = _get_trend(leads_qs, period)

    return result


def _compute_response_time(lead_ids):
    """
    Average hours from ASSIGNED to first post-assignment CONTACTED activity.
    Returns None when no qualifying lead pairs exist.
    """
    from apps.leads.models import LeadActivity

    if not lead_ids:
        return None

    activities = list(
        LeadActivity.objects
        .filter(lead_id__in=lead_ids, action__in=('assigned', 'contacted'))
        .order_by('lead_id', 'created_at')
        .values('lead_id', 'action', 'created_at')
    )

    by_lead = {}
    for act in activities:
        by_lead.setdefault(act['lead_id'], []).append(act)

    gaps = []
    for acts in by_lead.values():
        assigned_at = None
        for act in acts:
            if act['action'] == 'assigned' and assigned_at is None:
                assigned_at = act['created_at']
            elif act['action'] == 'contacted' and assigned_at is not None:
                gaps.append((act['created_at'] - assigned_at).total_seconds() / 3600)
                break  # first response per lead only

    return round(sum(gaps) / len(gaps), 1) if gaps else None


def compute_leaderboard(agents_qs):
    """
    Return agents ranked by conversion_rate desc. Accepts a pre-filtered queryset.
    Three queries total regardless of agent count.
    """
    from apps.leads.models import Lead, LeadActivity

    agents = list(agents_qs)  # evaluate once
    agent_ids = [a.id for a in agents]
    if not agent_ids:
        return []

    leads_qs     = Lead.objects.filter(assigned_agent_id__in=agent_ids)
    all_lead_ids = list(leads_qs.values_list('id', flat=True))

    total_by_agent = dict(
        leads_qs.values('assigned_agent_id')
                .annotate(c=Count('id'))
                .values_list('assigned_agent_id', 'c')
    )
    closed_by_agent = dict(
        leads_qs.filter(routing_state=Lead.RoutingState.CLOSED)
                .values('assigned_agent_id')
                .annotate(c=Count('id'))
                .values_list('assigned_agent_id', 'c')
    )

    lead_to_agent = dict(leads_qs.values_list('id', 'assigned_agent_id'))
    activities = list(
        LeadActivity.objects
        .filter(lead_id__in=all_lead_ids, action__in=('assigned', 'contacted'))
        .order_by('lead_id', 'created_at')
        .values('lead_id', 'action', 'created_at')
    ) if all_lead_ids else []

    acts_by_lead = {}
    for act in activities:
        acts_by_lead.setdefault(act['lead_id'], []).append(act)

    gaps_by_agent = {}
    for lead_id, acts in acts_by_lead.items():
        agent_id = lead_to_agent.get(lead_id)
        if agent_id is None:
            continue
        assigned_at = None
        for act in acts:
            if act['action'] == 'assigned' and assigned_at is None:
                assigned_at = act['created_at']
            elif act['action'] == 'contacted' and assigned_at is not None:
                gaps_by_agent.setdefault(agent_id, []).append(
                    (act['created_at'] - assigned_at).total_seconds() / 3600
                )
                break

    rows = []
    for agent in agents:  # use already-evaluated list
        total  = total_by_agent.get(agent.id, 0)
        closed = closed_by_agent.get(agent.id, 0)
        ag     = gaps_by_agent.get(agent.id, [])
        rows.append({
            'agent_id':               agent.id,
            'name':                   agent.name,
            'total_leads':            total,
            'conversion_rate':        round(closed / total * 100, 1) if total else 0.0,
            'avg_response_time_hours': round(sum(ag) / len(ag), 1) if ag else None,
            'closed_deals':           agent.closed_deals,
            'rating':                 float(agent.rating),
        })

    rows.sort(key=lambda r: r['conversion_rate'], reverse=True)
    for i, r in enumerate(rows):
        r['rank'] = i + 1

    return rows


def _get_trend(qs, period):
    now = timezone.now()
    if period == 'monthly':
        cutoff = now - datetime.timedelta(days=182)
        rows   = (
            qs.filter(created_at__gte=cutoff)
              .annotate(bucket=TruncMonth('created_at'))
              .values('bucket').annotate(count=Count('id')).order_by('bucket')
        )
        return [{'period': r['bucket'].strftime('%Y-%m'), 'count': r['count']} for r in rows]
    cutoff = now - datetime.timedelta(weeks=8)
    rows   = (
        qs.filter(created_at__gte=cutoff)
          .annotate(bucket=TruncWeek('created_at'))
          .values('bucket').annotate(count=Count('id')).order_by('bucket')
    )
    return [{'period': r['bucket'].strftime('%Y-%m-%d'), 'count': r['count']} for r in rows]
