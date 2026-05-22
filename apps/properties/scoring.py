"""
Deterministic property scoring engine for the Pakistani market.

Runs before the AI call to compute structured signals. These signals are:
  1. Passed to the AI prompt so Gemini has concrete anchors to reason against.
  2. Used as a standalone score if the AI is unavailable.

Score range: 0–100.
"""

# ── Location tiers ────────────────────────────────────────────────────────────
# Keywords matched case-insensitively against "{city} {location}".
# Longest-match wins (checked in order: tier_1 first).

TIER_1_KEYWORDS = [
    # Lahore
    'dha lahore', 'dha phase', 'gulberg', 'model town', 'bahria town lahore',
    'garden town', 'johar town', 'valencia', 'cantt lahore',
    # Islamabad / Rawalpindi
    'f-6', 'f-7', 'f-8', 'f-10', 'f-11', 'e-7', 'e-11',
    'blue area', 'margalla', 'sector e', 'sector f',
    'dha islamabad', 'bahria town islamabad',
    # Karachi
    'dha karachi', 'clifton', 'defence karachi', 'bath island',
    'phase 6', 'phase 7', 'phase 8',
    # General premium markers
    'phase 1', 'phase 2', 'phase 3',
]

TIER_2_KEYWORDS = [
    # Lahore
    'allama iqbal town', 'samanabad', 'township', 'wapda town', 'paragon city',
    'ferozepur road', 'canal road',
    # Islamabad
    'g-6', 'g-7', 'g-8', 'g-9', 'g-10', 'g-11', 'g-13',
    'rawalpindi', 'satellite town', 'bahria phase',
    # Karachi
    'gulshan', 'north karachi', 'federal b area', 'nazimabad', 'gulistan',
    # Other cities
    'faisalabad', 'multan', 'gujranwala', 'sialkot', 'peshawar',
]

# Everything else defaults to tier_3.


# ── Price benchmarks (PKR per marla) ─────────────────────────────────────────
# Used only for residential / plot types. Commercial is excluded.

PRICE_BENCHMARKS_PKR_PER_MARLA = {
    'tier_1': 4_500_000,
    'tier_2': 1_800_000,
    'tier_3':   700_000,
}


def _location_tier(city: str, location: str) -> str:
    text = f"{city} {location}".lower()
    for kw in TIER_1_KEYWORDS:
        if kw in text:
            return 'tier_1'
    for kw in TIER_2_KEYWORDS:
        if kw in text:
            return 'tier_2'
    return 'tier_3'


def _price_signal(price, area_marla, tier: str, property_type: str, city: str = '', country: str = 'PK') -> str:
    """Returns 'fair', 'underpriced', 'overpriced', or 'unknown'."""
    if not price or not area_marla or float(area_marla) <= 0:
        return 'unknown'
    if property_type == 'commercial':
        return 'unknown'  # no commercial benchmark

    actual_ppm = int(price) / float(area_marla)

    # PK-only hardcoded fallback; non-PK markets need a DB row or we can't score
    benchmark: int | None = PRICE_BENCHMARKS_PKR_PER_MARLA[tier] if country == 'PK' else None
    try:
        from apps.audit.models import AuditBenchmark
        qs = AuditBenchmark.objects.filter(country=country, size_unit='marla')
        if city:
            qs = qs.filter(city__iexact=city)
        if not qs.exists():
            qs = AuditBenchmark.objects.filter(country=country, location_key=tier, size_unit='marla')
        if qs.exists():
            bm = qs.first()
            benchmark = (bm.price_per_unit_min + bm.price_per_unit_max) // 2
    except Exception:
        pass

    if benchmark is None:
        return 'unknown'

    ratio = actual_ppm / benchmark
    if ratio < 0.70:
        return 'underpriced'
    if ratio > 1.40:
        return 'overpriced'
    return 'fair'


def _completeness(prop) -> int:
    """Returns 0–100 based on how many key fields are filled."""
    fields = [
        prop.title, prop.description, prop.city, prop.location,
        prop.area_marla, prop.price, prop.property_type,
        prop.construction_status, prop.furnished_status,
    ]
    filled = sum(1 for f in fields if f not in (None, '', 0))
    return int((filled / len(fields)) * 100)


class PropertyScoringEngine:

    @classmethod
    def compute_signals(cls, prop) -> dict:
        """
        Returns a signals dict that can be:
        - Passed to the AI prompt for richer context
        - Used directly to compute a deterministic score
        """
        tier          = _location_tier(prop.city or '', prop.location or '')
        org_country   = getattr(getattr(prop, 'organization', None), 'country', 'PK')
        price_signal  = _price_signal(prop.price, prop.area_marla, tier, prop.property_type, city=prop.city or '', country=org_country)
        completeness  = _completeness(prop)

        # Count linked passing verifications
        try:
            passed_verifs = prop.verifications.filter(status='passed').count()
            doc_scans     = sum(
                v.document_scans.count()
                for v in prop.verifications.prefetch_related('document_scans').all()
            )
        except Exception:
            passed_verifs = 0
            doc_scans     = 0

        return {
            'location_tier':        tier,
            'price_signal':         price_signal,
            'completeness_pct':     completeness,
            'legal_status':         prop.legal_status,
            'construction_status':  prop.construction_status or 'unknown',
            'furnished_status':     prop.furnished_status or 'unknown',
            'passed_verifications': passed_verifs,
            'document_count':       doc_scans,
        }

    @classmethod
    def deterministic_score(cls, signals: dict) -> int:
        """
        Compute a 0–100 score from signals alone, no AI required.
        Used as a fallback and as a sanity anchor for the AI score.
        """
        score = 50  # neutral baseline

        # legal status
        legal_delta = {
            'verified':   +20,
            'pending':    +5,
            'unverified':  0,
            'disputed':   -25,
        }
        score += legal_delta.get(signals['legal_status'], 0)

        # location tier
        tier_delta = {'tier_1': +15, 'tier_2': +5, 'tier_3': -5}
        score += tier_delta.get(signals['location_tier'], 0)

        # price vs benchmark
        price_delta = {'fair': +10, 'underpriced': +5, 'overpriced': -12, 'unknown': 0}
        score += price_delta.get(signals['price_signal'], 0)

        # construction status
        construction_delta = {
            'ready':              +10,
            'builder':            +5,
            'under_construction': -5,
            'unknown':             0,
        }
        score += construction_delta.get(signals['construction_status'], 0)

        # listing completeness
        c = signals['completeness_pct']
        if c >= 90:
            score += 10
        elif c >= 70:
            score += 5
        elif c < 50:
            score -= 5

        # verified documents
        if signals['passed_verifications'] > 0:
            score += 8
        if signals['document_count'] >= 2:
            score += 4

        return max(0, min(100, score))

    @classmethod
    def risk_from_score(cls, score: int) -> str:
        if score >= 70:
            return 'low'
        if score >= 45:
            return 'medium'
        return 'high'
