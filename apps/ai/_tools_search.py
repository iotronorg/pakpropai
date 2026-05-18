import logging
from ._tools_context import _ctx_phone

logger = logging.getLogger(__name__)


def search_properties(
    city: str,
    property_type: str = '',
    area_marla: float = 0.0,
    max_price_pkr: int = 0,
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
        max_price_pkr: Maximum budget in Pakistani Rupees. Use 0 if not specified.
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
            max_price=max_price_pkr if max_price_pkr > 0 else None,
            property_type=property_type,
            furnished_status=furnished,
            construction_status=construction_status,
            phone=phone,
        )
        if not results:
            return {
                'count': 0,
                'message': (
                    'No properties in our database match your criteria right now. '
                    'Live listings from Zameen and Graana are being fetched — '
                    'you will receive them in a follow-up message shortly.'
                ),
                'properties': [],
                'live_search_pending': True,
            }
        db_only = all(r.source == 'pakprop' for r in results)
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
                    'price_pkr': r.price_pkr or 0,
                    'price_formatted': f"PKR {r.price_pkr:,}" if r.price_pkr else 'Price not listed',
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
