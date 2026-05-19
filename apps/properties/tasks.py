import logging
from datetime import timedelta
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Re-use leads with status in these states for listing match alerts
_ALERT_STATUSES = ('new', 'warm', 'qualified')
# Intents that have no interest in buying/renting new listings
_SKIP_INTENTS = ('sell', 'tax')
# Max number of leads to notify per new property (prevents spam on popular cities)
_MAX_ALERT_RECIPIENTS = 50
# A property is "new" if it was created within this window (task may queue briefly)
_NEW_PROPERTY_WINDOW = timedelta(minutes=10)


@shared_task
def warm_search_cache(
    cache_key: str,
    city: str,
    location: str,
    area_marla,
    max_price,
    property_type: str,
    phone: str = '',
):
    """
    Runs all scrapers in the background, caches the combined results, and sends a
    WhatsApp follow-up to the user's phone if live listings were found.
    Called automatically on a cache miss so the WhatsApp reply path is never blocked.
    """
    from apps.config.services import SystemConfigService
    if not SystemConfigService.scraper_enabled():
        return 0

    from django.core.cache import cache
    from .scrapers.registry import search_all_scrapers

    try:
        results = search_all_scrapers(
            city=city, location=location, area_marla=area_marla,
            max_price=max_price, property_type=property_type,
        )
    except Exception as exc:
        logger.error(f"warm_search_cache: scraper failed key={cache_key}: {exc}")
        return 0

    if results:
        cache.set(cache_key, [r.to_dict() for r in results], timeout=1800)
    else:
        cache.set(cache_key, [], timeout=900)  # cache empty so we don't thrash

    logger.info(f"warm_search_cache: {len(results)} result(s) cached key={cache_key}")

    if phone and results:
        _send_scraped_followup(phone, results[:5])

    return len(results)


def _send_scraped_followup(phone: str, results: list):
    """Send a brief WhatsApp message with live scraped listings."""
    try:
        from apps.whatsapp.client import WhatsAppClient
        count = len(results)
        lines = [f"🔍 Found {count} live listing{'s' if count != 1 else ''} from Zameen/Graana:\n"]
        for i, r in enumerate(results, 1):
            lines.append(r.format_wa(index=i))
        WhatsAppClient.send_text(phone, "\n\n".join(lines), skip_window_check=True)
        logger.info(f"Sent scraped follow-up to phone={phone} ({count} results)")
    except Exception as exc:
        logger.warning(f"_send_scraped_followup failed phone={phone}: {exc}")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def score_property_task(self, property_id: str):
    from .models import Property
    from .scoring import PropertyScoringEngine
    from apps.ai.scoring import score_property as _ai_score_property

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
        result = _ai_score_property(prop)
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

    # Only alert on fresh listings — nightly rescores must not re-notify
    if timezone.now() - prop.created_at < _NEW_PROPERTY_WINDOW:
        _notify_matching_leads(prop)


def _notify_matching_leads(prop) -> int:
    """
    Send a WhatsApp listing alert to active leads whose city and budget match
    this newly listed property. Capped at _MAX_ALERT_RECIPIENTS to prevent spam.
    """
    try:
        from django.db.models import Q
        from apps.leads.models import Lead
        from apps.whatsapp.client import WhatsAppClient

        city_lower = (prop.city or '').strip().lower()
        if not city_lower:
            return 0

        candidates = (
            Lead.objects
            .filter(
                city_interest__icontains=city_lower,
                city_interest__gt='',
                status__in=_ALERT_STATUSES,
            )
            .exclude(intent__in=_SKIP_INTENTS)
            .select_related('user')
        )

        # Budget gate: only notify if lead's max budget covers this price (same currency)
        if prop.price:
            candidates = candidates.filter(
                Q(budget_max__isnull=True) |
                Q(budget_max__gte=prop.price, budget_currency=prop.currency)
            )

        sent = 0
        for lead in candidates[:_MAX_ALERT_RECIPIENTS]:
            if not lead.user or not lead.user.phone:
                continue
            try:
                area_str  = f"{prop.area_marla} {prop.area_unit}" if prop.area_marla else ''
                price_str = f"{prop.currency} {prop.price:,}" if prop.price else 'Price on request'
                area_line = f"📐 {area_str}\n" if area_str else ''
                msg = (
                    f"🏠 *New Listing in {prop.city}!*\n\n"
                    f"A property matching your search just went live:\n\n"
                    f"📋 *{prop.title}*\n"
                    f"📍 {prop.location}\n"
                    f"{area_line}"
                    f"💰 {price_str}\n\n"
                    "Reply here to get full details or book a visit! 🔑"
                )
                WhatsAppClient.send_text(lead.user.phone.lstrip('+'), msg)
                sent += 1
            except Exception as exc:
                logger.warning(f"Listing alert WA failed lead={lead.id}: {exc}")

        logger.info(f"Sent {sent} listing alert(s) for property {prop.id} ({prop.city})")
        return sent
    except Exception as exc:
        logger.error(f"_notify_matching_leads failed for property {prop.id}: {exc}")
        return 0


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
