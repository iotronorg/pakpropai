import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def score_property_task(self, property_id: str):
    from .models import Property
    from .scoring import PropertyScoringEngine
    from services.ai_orchestrator import AIOrchestrator

    try:
        prop = Property.objects.prefetch_related(
            'verifications__document_scans'
        ).get(id=property_id)
    except Property.DoesNotExist:
        logger.warning(f"score_property: {property_id} not found")
        return

    # Always compute deterministic signals first — these are free and instant.
    signals  = PropertyScoringEngine.compute_signals(prop)
    baseline = PropertyScoringEngine.deterministic_score(signals)

    try:
        result = AIOrchestrator.score_property(prop)
        score  = int(result.get('score') or baseline)
        risk   = result.get('risk') or PropertyScoringEngine.risk_from_score(score)
    except Exception as exc:
        logger.warning(f"AI scoring unavailable for {property_id}, using deterministic score: {exc}")
        score  = baseline
        risk   = PropertyScoringEngine.risk_from_score(baseline)
        result = {
            'score':   score,
            'risk':    risk,
            'factors': _signals_to_factors(signals),
            'suggestion': 'Based on market signals. AI analysis unavailable.',
            'source':  'deterministic',
        }

    prop.ai_score   = score
    prop.risk_level = risk
    prop.ai_analysis = {**result, 'signals': signals}
    prop.save(update_fields=['ai_score', 'risk_level', 'ai_analysis'])
    logger.info(f"Scored {property_id}: {score}/100 [{risk}] source={result.get('source','ai')}")


def _signals_to_factors(signals: dict) -> list:
    factors = []
    tier_labels = {'tier_1': 'Premium location', 'tier_2': 'Mid-tier location', 'tier_3': 'Developing area'}
    factors.append(tier_labels.get(signals['location_tier'], 'Location assessed'))

    price_labels = {
        'fair':        'Price is in line with area benchmark',
        'underpriced': 'Priced below area benchmark — potential value buy',
        'overpriced':  'Priced above area benchmark',
        'unknown':     'Insufficient data for price comparison',
    }
    factors.append(price_labels.get(signals['price_signal'], 'Price assessed'))

    if signals['legal_status'] == 'verified':
        factors.append('Property is legally verified')
    elif signals['legal_status'] == 'disputed':
        factors.append('Legal status is disputed — high risk')

    return factors


@shared_task
def rescore_all_properties_task():
    """Re-score all active properties. Run from admin or management command."""
    from .models import Property
    ids = list(Property.objects.filter(is_active=True).values_list('id', flat=True))
    for pid in ids:
        score_property_task.delay(str(pid))
    logger.info(f"Queued rescore for {len(ids)} properties")
    return len(ids)
