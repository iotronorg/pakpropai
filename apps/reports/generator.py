import logging
from django.db.models import Avg, Count

logger = logging.getLogger(__name__)


def generate_monthly_report_content(org, start_dt, end_dt) -> dict:
    """
    Compute aggregated stats for org over [start_dt, end_dt).
    Returns a plain dict — no IO or PDF logic.
    """
    return {
        'leads':      _leads_section(org, start_dt, end_dt),
        'top_agents': _top_agents_section(org, start_dt, end_dt),
        'deals':      _deals_section(org, start_dt, end_dt),
        'properties': _properties_section(org, start_dt, end_dt),
    }


def _leads_section(org, start_dt, end_dt) -> dict:
    from apps.leads.models import Lead

    qs = Lead.objects.filter(organization=org, created_at__gte=start_dt, created_at__lt=end_dt)
    total = qs.count()
    by_status = dict(
        qs.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
    )
    qualified = qs.filter(routing_state='closed').count()
    avg_score = qs.aggregate(avg=Avg('score'))['avg'] or 0
    conversion_rate = round(qualified / total * 100, 1) if total else 0.0

    return {
        'total':           total,
        'by_status':       by_status,
        'qualified':       qualified,
        'conversion_rate': conversion_rate,
        'avg_score':       round(float(avg_score), 1),
    }


def _top_agents_section(org, start_dt, end_dt) -> list:
    from apps.agents.models import Agent

    agents = (
        Agent.objects
        .filter(organization=org, is_active=True)
        .select_related('user')
        .order_by('-closed_deals')[:3]
    )
    return [
        {
            'name':         a.name,
            'closed_deals': a.closed_deals,
            'rating':       float(a.rating),
        }
        for a in agents
    ]


def _deals_section(org, start_dt, end_dt) -> dict:
    from apps.escrow.models import EscrowDeal

    qs = EscrowDeal.objects.filter(
        property__organization=org,
        created_at__gte=start_dt,
        created_at__lt=end_dt,
    )
    status_counts = dict(
        qs.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
    )
    return {
        'total':    qs.count(),
        'completed': status_counts.get('released', 0),
        'expired':   status_counts.get('expired', 0),
        'disputed':  status_counts.get('disputed', 0),
        'by_status': status_counts,
    }


def _properties_section(org, start_dt, end_dt) -> dict:
    from apps.properties.models import Property

    qs = Property.objects.filter(
        organization=org,
        created_at__gte=start_dt,
        created_at__lt=end_dt,
    )
    by_type = dict(
        qs.values_list('property_type').annotate(c=Count('id')).values_list('property_type', 'c')
    )
    avg_score = qs.aggregate(avg=Avg('ai_score'))['avg'] or 0
    return {
        'new_listings': qs.count(),
        'avg_ai_score': round(float(avg_score), 1),
        'by_type':      by_type,
    }
