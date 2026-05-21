"""
Property Recommendations Engine — Phase 9.

Two public functions:
  recommend_for_lead(lead, limit=5)  → ranked Property list matching a lead's profile
  similar_to(prop, limit=5)          → similar active listings (same city/type, close price)
"""
import logging

logger = logging.getLogger(__name__)

# Maps lead intent → preferred property types (order matters: best first)
_INTENT_TYPES: dict[str, list[str]] = {
    'buy':    ['residential', 'plot', 'commercial'],
    'rent':   ['residential', 'commercial'],
    'invest': ['commercial', 'plot', 'residential'],
    'sell':   [],  # seller leads — no type restriction
}

# Points awarded per property_type when matched against a lead's intent
_TYPE_POINTS: dict[str, dict[str, int]] = {
    'buy':    {'residential': 20, 'plot': 15, 'commercial': 10, 'industrial': 5},
    'rent':   {'residential': 20, 'commercial': 15},
    'invest': {'commercial': 20, 'plot': 18, 'residential': 10, 'industrial': 12},
}


# ── Public API ────────────────────────────────────────────────────────────────

def recommend_for_lead(lead, limit: int = 5) -> list:
    """
    Return up to `limit` Property objects ranked by relevance to `lead`.

    Scoring (100 pts max):
      City match      40 pts  — exact or substring city overlap
      Type × intent   20 pts  — property type aligned with lead intent
      Budget fit      25 pts  — price within lead budget range (partial credit for near-misses)
      AI quality      15 pts  — property.ai_score contribution
    """
    from apps.properties.models import Property

    try:
        qs = Property.objects.filter(is_active=True).select_related('organization')

        if lead.organization_id:
            qs = qs.filter(organization_id=lead.organization_id)

        city = (lead.city_interest or '').strip().lower()
        if city:
            qs = qs.filter(city__icontains=city)

        if lead.budget_max:
            qs = qs.filter(price__lte=int(lead.budget_max * 1.2))
        if lead.budget_min:
            qs = qs.filter(price__gte=int(lead.budget_min * 0.8))

        allowed = _INTENT_TYPES.get(lead.intent or '', [])
        if allowed:
            qs = qs.filter(property_type__in=allowed)

        # Fetch up to 100 candidates (pre-sorted by AI score so we don't pull bad listings)
        candidates = list(qs.order_by('-ai_score')[:100])
        ranked = sorted(
            ((_relevance_score(p, lead, city), p) for p in candidates),
            key=lambda x: x[0],
            reverse=True,
        )
        return [p for _, p in ranked[:limit]]

    except Exception as exc:
        logger.error(f"recommend_for_lead failed lead={getattr(lead, 'id', '?')}: {exc}")
        return []


def similar_to(prop, limit: int = 5) -> list:
    """
    Return up to `limit` active properties similar to `prop`.
    Matches on same city + property_type; prefers ±30% price range.
    """
    from apps.properties.models import Property

    try:
        qs = (
            Property.objects
            .filter(is_active=True, city__iexact=prop.city, property_type=prop.property_type)
            .exclude(id=prop.id)
            .select_related('organization')
        )

        if prop.price:
            price_range = qs.filter(
                price__gte=int(prop.price * 0.70),
                price__lte=int(prop.price * 1.30),
            ).order_by('-ai_score')
            if price_range.count() >= limit:
                return list(price_range[:limit])

        return list(qs.order_by('-ai_score')[:limit])

    except Exception as exc:
        logger.error(f"similar_to failed property={getattr(prop, 'id', '?')}: {exc}")
        return []


# ── Scoring internals ─────────────────────────────────────────────────────────

def _relevance_score(prop, lead, city_lower: str) -> float:
    score = 0.0

    # City match (40 pts)
    if city_lower:
        pc = prop.city.lower()
        if city_lower in pc or pc in city_lower:
            score += 40

    # Property type × intent (up to 20 pts)
    score += _TYPE_POINTS.get(lead.intent or '', {}).get(prop.property_type, 0)

    # Budget fit (25 pts, proportional for near-misses)
    if prop.price and (lead.budget_min or lead.budget_max):
        bmin = float(lead.budget_min or 0)
        bmax = float(lead.budget_max) if lead.budget_max else float('inf')
        if bmin <= prop.price <= bmax:
            score += 25
        elif prop.price < bmin and bmin > 0:
            score += 25 * (prop.price / bmin)
        elif bmax < float('inf') and prop.price > bmax:
            score += 25 * (bmax / prop.price)

    # AI quality bonus (up to 15 pts)
    if prop.ai_score:
        score += prop.ai_score * 0.15

    return score
