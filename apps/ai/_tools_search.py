import logging
from ._tools_context import _ctx_conversation, _ctx_org, _ctx_phone
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
    purpose: str = '',
    bedrooms: int = 0,
    amenities: str = '',
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
        purpose: 'buy' or 'rent' when the user says so (rent, kiraya, lease → 'rent'). Leave blank if unclear.
        bedrooms: Minimum number of bedrooms the user asked for. Use 0 if not specified.
        amenities: Comma-separated amenities the user requires, as keys, e.g. 'gym,lift'.
            Known keys include gym, swimming_pool, private_pool, lift, covered_parking,
            security_guard, cctv, gated_community, backup_generator, solar_panels,
            gas_connection, garden, balcony, servant_quarters, air_conditioning,
            wheelchair_access. Leave blank if none were asked for.
    """
    try:
        from apps.properties.search import PropertySearchService
        # A web chat must not trigger WhatsApp follow-ups or touch that session's focus (T-2020).
        conversation = _ctx_conversation.get()
        phone = '' if conversation is not None and conversation.channel == 'web' else _ctx_phone.get() or ''
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
            purpose=purpose,
            min_bedrooms=bedrooms if bedrooms and bedrooms > 0 else None,
            amenities=amenities,
        )
        from apps.config.services import SystemConfigService
        from apps.properties.search import _scrapers_apply, resolve_amenities
        # Scrapers only fetch sale listings (T-1070a) and have no amenity data
        # (T-1070d): no follow-up for rent/lease or amenity searches.
        scraping = (SystemConfigService.scraper_enabled()
                    and _scrapers_apply(purpose, resolve_amenities(amenities)))
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
                    'currency': r.currency or '',
                    'price_formatted': ('Price on request' if r.price_on_request
                                        else format_price(r.price, r.currency)),
                    'price_type': r.price_type,
                    'purpose': r.purpose,
                    'bedrooms': r.bedrooms,
                    'bathrooms': r.bathrooms,
                    'property_type': r.property_type,
                    'amenities': r.amenities,
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
