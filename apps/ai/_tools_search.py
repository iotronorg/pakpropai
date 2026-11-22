import logging
from ._tools_context import _ctx_org, _ctx_phone
from apps.markets.services import format_price

logger = logging.getLogger(__name__)


def search_properties(
    city: str,
    property_type: str = '',
    area_marla: float = 0.0,
    max_price: int = 0,
    location: str = '',
    furnished: str = '',
    construction_status: str = '',
) -> dict:
    """
    Search for available property listings in Pakistan (both database and live scraped results).
    Call this whenever the user wants to find, buy, or rent a property.

    Args:
        city: City name e.g. 'Lahore', 'Karachi', 'Islamabad', 'Rawalpindi'
        property_type: One of 'plot', 'residential', 'commercial'. Leave blank for all types.
        area_marla: Property size in Marla. 1 Kanal = 20 Marla. Use 0 if not specified.
        max_price: Maximum budget in Pakistani Rupees. Use 0 if not specified.
        location: Specific area/society e.g. 'DHA Phase 6', 'Gulberg 3', 'Bahria Town Block D'
        furnished: One of 'furnished', 'semi_furnished', 'unfurnished'. Leave blank if not specified.
        construction_status: One of 'builder', 'ready', 'under_construction'. Leave blank if not specified.
    """
    try:
        from apps.properties.search import PropertySearchService
        phone = _ctx_phone.get() or ''
        results = PropertySearchService.search(
            city=city,
            location=location,
            area_marla=area_marla if area_marla > 0 else None,
            max_price=max_price if max_price > 0 else None,
            property_type=property_type,
            furnished_status=furnished,
            construction_status=construction_status,
            phone=phone,
            viewer_org=_ctx_org.get(),
        )
        from apps.config.services import SystemConfigService
        scraping = SystemConfigService.scraper_enabled()
        if not results:
            return {
                'count': 0,
                'message': (
                    'No properties in our database match your criteria right now. '
                    + ('More listings are being fetched — you will receive them in a '
                       'follow-up message shortly.' if scraping else
                       'Suggest widening the area, budget or property type.')
                ),
                'properties': [],
                'live_search_pending': scraping,
            }
        db_only = scraping and all(r.source == 'pakprop' for r in results)
        _register_compliance_context(results, phone)
        return {
            'count': len(results),
            'live_search_pending': db_only,
            'properties': [
                {
                    'id': r.source_id,
                    'title': r.title,
                    'city': r.city,
                    'location': r.location,
                    'area_marla': str(r.area_marla) if r.area_marla else 'N/A',
                    'price': r.price or 0,
                    'price_formatted': format_price(r.price, r.currency),
                    'property_type': r.property_type,
                    'source': r.source,
                    'url': r.url or '',
                    'ai_verdict': getattr(r, 'ai_verdict', ''),
                }
                for r in results[:5]
            ],
        }
    except Exception as exc:
        logger.error(f"search_properties tool failed: {exc}")
        return {'count': 0, 'error': 'Search temporarily unavailable.', 'properties': []}


def _register_compliance_context(results, phone: str) -> None:
    """
    When exactly one own-DB listing is in focus, register it as ground truth for
    ComplianceEngine so the LLM's reply about it is checked for price, currency,
    installment and city claims (T-0009). Ambiguous result sets are not registered.
    """
    org = _ctx_org.get()
    if not phone or org is None:
        return
    own = [r for r in results if r.source == 'pakprop' and r.source_id]
    if len(results) != 1 or len(own) != 1:
        return
    try:
        from apps.ai.compliance_filters import ComplianceEngine, _load_property_by_id
        prop = _load_property_by_id(own[0].source_id)
        if prop:
            ComplianceEngine.set_active_property(phone, str(org.pk), prop)
    except Exception as exc:
        logger.warning("compliance context registration failed: %s", exc)
