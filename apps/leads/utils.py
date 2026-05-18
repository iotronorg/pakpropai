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
                             'budget_max', 'score', 'last_scored_at']
            if intent:        lead.intent        = intent
            if city_interest: lead.city_interest  = city_interest
            if budget_min:    lead.budget_min     = budget_min
            if budget_max:    lead.budget_max     = budget_max
            if organization is not None and not lead.organization_id:
                lead.organization = organization
                update_fields.append('organization')
            lead.score = min(lead.score + 5, 100)
            lead.save(update_fields=update_fields)
    except Exception:
        logger.exception("Lead upsert failed")
