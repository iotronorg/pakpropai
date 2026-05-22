import re
import logging

logger = logging.getLogger(__name__)


def parse_pkr(text: str):
    """Extract a PKR amount from a natural-language string. Returns int or None."""
    t = text.lower().replace(',', '').replace('rs.', '').replace('pkr', '')
    for pattern, multiplier in [
        (r'(\d+(?:\.\d+)?)\s*crore',       10_000_000),
        (r'(\d+(?:\.\d+)?)\s*(?:lakh|lac)', 100_000),
        (r'(\d+(?:\.\d+)?)\s*million',      1_000_000),
        (r'(\d+(?:\.\d+)?)\s*k\b',          1_000),
    ]:
        m = re.search(pattern, t)
        if m:
            return int(float(m.group(1)) * multiplier)
    m = re.search(r'\b(\d{5,})\b', t)
    return int(m.group(1)) if m else None


def compute_score_factors(lead) -> dict:
    from django.utils.timezone import now
    intent_pts   = 25 if lead.intent in ('buy', 'invest', 'sell') else (15 if lead.intent else 0)
    budget_pts   = 20 if (lead.budget_min and lead.budget_max) else (10 if lead.budget_max else 0)
    location_pts = 15 if lead.city_interest else 0
    msg_count    = lead.messages.count()
    engage_pts   = min(25, msg_count * 3)
    days_ago     = (now() - lead.last_contacted_at).days if lead.last_contacted_at else 30
    recency_pts  = max(0, 15 - days_ago)
    total        = intent_pts + budget_pts + location_pts + engage_pts + recency_pts
    return {
        'intent':     intent_pts,
        'budget':     budget_pts,
        'location':   location_pts,
        'engagement': engage_pts,
        'recency':    recency_pts,
        'total':      total,
    }


def upsert_lead(user, intent: str, city_interest: str = '',
                budget_min: int = None, budget_max: int = None,
                organization=None):
    """Create or update the CRM lead record for a WhatsApp user."""
    from apps.leads.models import Lead
    try:
        defaults = {
            'intent':        intent,
            'city_interest': city_interest,
            'budget_min':    budget_min,
            'budget_max':    budget_max,
            'score':         10,
        }
        if organization is not None:
            defaults['organization'] = organization
        lead, created = Lead.objects.get_or_create(user=user, defaults=defaults)
        if not created:
            update_fields = ['intent', 'city_interest', 'budget_min',
                             'budget_max', 'score', 'last_scored_at', 'score_factors']
            if intent:        lead.intent        = intent
            if city_interest: lead.city_interest  = city_interest
            if budget_min:    lead.budget_min     = budget_min
            if budget_max:    lead.budget_max     = budget_max
            if organization is not None and not lead.organization_id:
                lead.organization = organization
                update_fields.append('organization')
            lead.score = min(lead.score + 5, 100)
            lead.score_factors = compute_score_factors(lead)
            lead.save(update_fields=update_fields)
        else:
            lead.score_factors = compute_score_factors(lead)
            lead.save(update_fields=['score_factors'])
    except Exception:
        logger.exception("Lead upsert failed")
