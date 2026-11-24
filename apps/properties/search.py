"""
Unified property search — merges local DB listings with scraped web results.
All WhatsApp property search flows go through this service.

Scraper results are cache-first: on a Redis hit (30-min TTL) they return instantly;
on a miss the request returns DB-only results immediately and a Celery task runs the
scrapers in the background, caches results, then sends a WhatsApp follow-up.
"""
import logging
from dataclasses import dataclass
from .models import Property
from .taxonomy import active_amenity_keys, filter_all_amenities, parse_amenity_keys
from .scrapers.base import PropertyResult, BaseScraper

from apps.markets.services import format_price
logger = logging.getLogger(__name__)

PAGE_SIZE = 3  # listings per WhatsApp page
_SCRAPER_CACHE_TTL = 1800  # 30 minutes


@dataclass
class SearchParams:
    """
    Normalized search parameters accepted from the AI layer or any caller.
    All fields are optional — supply only what is known.
    """
    city: str = ''
    location: str = ''
    property_type: str = ''
    purpose: str = ''                # 'buy'/'sale' | 'rent' | 'lease' | a Property.Purpose value | ''
    min_bedrooms: int = 0
    min_bathrooms: int = 0
    amenities: tuple = ()            # taxonomy keys, all must match; unknown keys are ignored
    min_price: int = 0
    max_price: int = 0
    area_marla: float = 0.0          # target size; DB query uses ±20 % tolerance band
    furnished_status: str = ''
    construction_status: str = ''
    installments: bool = False       # True → only installment-available listings
    legal_status: str = ''           # '' | 'verified' | 'pending' | ...
    organization_id: str = ''        # restrict results to one org's inventory
    viewer_org_id: str = ''          # org context of the requester (sandbox visibility, ADR-002)
    page: int = 0
    page_size: int = PAGE_SIZE
    phone: str = ''                  # forwarded to background scrape task

    @classmethod
    def from_dict(cls, params: dict) -> 'SearchParams':
        """Build from the AI layer's normalized tool-call argument dict."""
        return cls(
            city=str(params.get('city', '')),
            location=str(params.get('location', '')),
            property_type=str(params.get('property_type', '')),
            purpose=str(params.get('purpose', '')),
            min_bedrooms=int(params.get('min_bedrooms', 0) or params.get('bedrooms', 0) or 0),
            min_bathrooms=int(params.get('min_bathrooms', 0) or params.get('bathrooms', 0) or 0),
            amenities=tuple(parse_amenity_keys(params.get('amenities'))),
            min_price=int(params.get('min_price', 0) or 0),
            max_price=int(
                params.get('max_price', 0) or params.get('max_price', 0) or 0
            ),
            area_marla=float(params.get('area_marla', 0) or 0),
            furnished_status=str(
                params.get('furnished', '') or params.get('furnished_status', '')
            ),
            construction_status=str(params.get('construction_status', '')),
            installments=bool(params.get('installments', False)),
            legal_status=str(params.get('legal_status', '')),
            organization_id=str(params.get('organization_id', '')),
            viewer_org_id=str(params.get('viewer_org_id', '') or ''),
            page=int(params.get('page', 0) or 0),
            page_size=int(params.get('page_size', PAGE_SIZE) or PAGE_SIZE),
            phone=str(params.get('phone', '')),
        )


_PURPOSE_ALIASES = {
    'buy': ('sale',), 'sale': ('sale',), 'purchase': ('sale',),
    'rent': ('rent_long', 'rent_short'), 'rental': ('rent_long', 'rent_short'),
    'lease': ('lease_commercial',),
}


def purpose_values(purpose: str) -> tuple:
    """Map a caller's purpose ('buy', 'rent', a Property.Purpose value) to stored values; () = any."""
    p = (purpose or '').strip().lower()
    if p in Property.Purpose.values:
        return (p,)
    return _PURPOSE_ALIASES.get(p, ())


def _scrapers_apply(purpose: str, amenities=()) -> bool:
    """
    Scrapers only fetch sale listings (skip them for a rent/lease search) and
    carry no amenity data (skip them when an amenity is a hard filter, T-1070d).
    """
    values = purpose_values(purpose)
    return (not values or 'sale' in values) and not amenities


def resolve_amenities(amenities) -> tuple:
    """Known active amenity keys from a caller's list; unknown ones are dropped (soft, per the AI spec)."""
    keys = parse_amenity_keys(amenities)
    if not keys:
        return ()
    known = active_amenity_keys(keys)
    return tuple(k for k in keys if k in known)


class PropertySearchService:

    @classmethod
    def execute(cls, params: SearchParams) -> dict:
        """
        Primary entry point for the AI layer.
        Accepts a SearchParams object and returns a structured payload
        ready for the WhatsApp formatter or API layer.
        """
        amenities = resolve_amenities(params.amenities)
        db_results = cls._from_db(
            city=params.city,
            location=params.location,
            area_marla=params.area_marla or None,
            min_price=params.min_price or None,
            max_price=params.max_price or None,
            property_type=params.property_type,
            furnished_status=params.furnished_status,
            construction_status=params.construction_status,
            installments=params.installments or None,
            legal_status=params.legal_status,
            organization_id=params.organization_id,
            viewer_org=params.viewer_org_id or None,
            purpose=params.purpose,
            min_bedrooms=params.min_bedrooms or None,
            min_bathrooms=params.min_bathrooms or None,
            amenities=amenities,
        )
        scraped_results = cls._from_scrapers(
            city=params.city,
            location=params.location,
            area_marla=params.area_marla or None,
            max_price=params.max_price or None,
            property_type=params.property_type,
            phone=params.phone,
        ) if _scrapers_apply(params.purpose, amenities) else []

        all_results = cls._dedup(db_results + scraped_results)
        if all_results:
            cls._apply_ai_verdicts(all_results[:5])

        total = len(all_results)
        offset = params.page * params.page_size
        page_slice = all_results[offset: offset + params.page_size]

        from apps.config.services import SystemConfigService
        live_pending = SystemConfigService.scraper_enabled() and _scrapers_apply(params.purpose, amenities) and (
            total == 0 or all(r.source == 'pakprop' for r in all_results)
        )
        return {
            'query': {
                'city': params.city,
                'location': params.location,
                'property_type': params.property_type,
                'purpose': params.purpose,
                'min_bedrooms': params.min_bedrooms,
                'amenities': list(amenities),
                'max_price': params.max_price,
                'area_marla': params.area_marla,
                'furnished_status': params.furnished_status,
            },
            'pagination': {
                'page': params.page,
                'page_size': params.page_size,
                'total': total,
                'has_next': (offset + params.page_size) < total,
            },
            'results': [cls._result_to_dict(r) for r in page_slice],
            'live_search_pending': live_pending,
            'whatsapp_message': (
                cls.format_page(all_results, offset, total)
                if all_results
                else (
                    'No properties found right now. More listings are being fetched — '
                    'you will receive them in a follow-up message shortly.'
                    if live_pending else
                    'No listings match these criteria right now. Try widening the area, '
                    'budget or property type.'
                )
            ),
        }

    @classmethod
    def _result_to_dict(cls, r: PropertyResult) -> dict:
        return {
            'source': r.source,
            'source_id': r.source_id,
            'title': r.title,
            'city': r.city,
            'location': r.location,
            'area_marla': r.area_marla,
            'size_value': r.size_value,
            'size_unit': r.size_unit,
            'price': r.price,
            'price_formatted': format_price(r.price, r.currency),
            'currency': r.currency,
            'purpose': r.purpose,
            'price_type': r.price_type,
            'price_on_request': r.price_on_request,
            'bedrooms': r.bedrooms,
            'bathrooms': r.bathrooms,
            'property_type': r.property_type,
            'amenities': r.amenities,
            'furnished_status': r.furnished_status,
            'construction_status': r.construction_status,
            'url': r.url or '',
            'ai_score': r.ai_score,
            'ai_verdict': getattr(r, 'ai_verdict', ''),
        }

    @classmethod
    def search(cls, city='', location='', area_marla=None, max_price=None,
               property_type='', furnished_status='',
               construction_status='', phone: str = '',
               viewer_org=None, purpose='', min_bedrooms=None,
               min_bathrooms=None, amenities=()) -> list[PropertyResult]:
        """
        Returns merged + AI-scored results (DB first, then scraped).
        `phone` is forwarded to the background task so a follow-up WA message
        can be sent when live scraper results arrive.
        """
        amenities = resolve_amenities(amenities)
        db_results = cls._from_db(
            city=city, location=location, area_marla=area_marla,
            max_price=max_price, property_type=property_type,
            furnished_status=furnished_status, construction_status=construction_status,
            viewer_org=viewer_org, purpose=purpose,
            min_bedrooms=min_bedrooms, min_bathrooms=min_bathrooms, amenities=amenities,
        )
        scraped_results = cls._from_scrapers(
            city=city, location=location, area_marla=area_marla,
            max_price=max_price, property_type=property_type, phone=phone,
        ) if _scrapers_apply(purpose, amenities) else []

        all_results = cls._dedup(db_results + scraped_results)
        if not all_results:
            return []

        cls._apply_ai_verdicts(all_results[:5])  # AI score top 5 only (cost-aware)
        return all_results

    @classmethod
    def _search_cache_key(cls, city, location, area_marla, max_price, property_type) -> str:
        return BaseScraper.safe_cache_key(
            'prop_search',
            city=city or '', loc=location or '',
            area=area_marla or 0, price=max_price or 0, ptype=property_type or '',
        )

    # ── Sources ───────────────────────────────────────────────────────────────

    @classmethod
    def _from_db(
        cls, city='', location='', area_marla=None, max_price=None,
        property_type='', furnished_status='', construction_status='',
        min_price=None, installments=None, legal_status='', organization_id='',
        viewer_org=None, purpose='', min_bedrooms=None, min_bathrooms=None, amenities=(),
    ) -> list[PropertyResult]:
        qs = (
            Property.objects
            .visible_to(viewer_org)
            .select_related('organization', 'listed_by_agent')
            .prefetch_related('amenities')
            .only(
                'id', 'title', 'city', 'location', 'size_value', 'size_unit', 'size_m2',
                'price', 'currency',
                'category', 'furnished_status', 'construction_status',
                'ai_score', 'installment_available', 'legal_status',
                'organization_id', 'listed_by_agent_id',
                'purpose', 'price_type', 'price_on_request', 'bedrooms', 'bathrooms',
            )
        )

        if city:
            qs = qs.filter(city__icontains=city)
        if location:
            qs = qs.filter(location__icontains=location)
        if min_price:
            qs = qs.filter(price__gte=min_price)
        if max_price:
            qs = qs.filter(price__lte=max_price)
        if area_marla:
            # ±20 % band on the canonical m² (T-1070b): a kanal or sqft listing is
            # compared by real size, not by its raw number (KI-049).
            target_m2 = float(area_marla) * Property._SQM_FACTORS['marla']
            qs = qs.filter(size_m2__gte=target_m2 * 0.8, size_m2__lte=target_m2 * 1.2)
        if property_type:
            # Legacy type vocabulary (AI tool / WhatsApp): matched on category.
            from .taxonomy import categories_for_property_type
            cats = categories_for_property_type(property_type.strip().lower()) or \
                [c for c in Property.Category.values if property_type.strip().lower() in c]
            qs = qs.filter(category__in=cats)
        if furnished_status:
            qs = qs.filter(furnished_status=furnished_status)
        if construction_status:
            qs = qs.filter(construction_status=construction_status)
        if installments:
            qs = qs.filter(installment_available=True)
        if legal_status:
            qs = qs.filter(legal_status=legal_status)
        if organization_id:
            qs = qs.filter(organization_id=organization_id)
        if (purposes := purpose_values(purpose)):
            qs = qs.filter(purpose__in=purposes)
        if min_bedrooms:
            qs = qs.filter(bedrooms__gte=min_bedrooms)
        if min_bathrooms:
            qs = qs.filter(bathrooms__gte=min_bathrooms)
        if amenities:
            qs = filter_all_amenities(qs, amenities)

        return [
            PropertyResult(
                source              = 'pakprop',
                source_id           = str(p.id),
                title               = p.title,
                city                = p.city,
                location            = p.location,
                area_marla          = p.size_in('marla'),    # marla-equivalent (dedup/matching)
                size_value          = float(p.size_value) if p.size_value is not None else None,
                size_unit           = p.size_unit,
                price           = p.price,
                property_type       = p.property_type,
                furnished_status    = p.furnished_status,
                construction_status = p.construction_status,
                url                 = '',
                ai_score            = p.ai_score,
                currency            = p.currency,
                purpose             = p.purpose,
                price_type          = p.price_type,
                price_on_request    = p.price_on_request,
                bedrooms            = p.bedrooms,
                bathrooms           = p.bathrooms,
                verified            = p.legal_status == Property.LegalStatus.VERIFIED,
                amenities           = sorted(a.key for a in p.amenities.all()),
            )
            for p in qs.order_by('-ai_score', '-created_at')[:10]
        ]

    @classmethod
    def _from_scrapers(cls, city, location, area_marla, max_price, property_type,
                       phone: str = '') -> list[PropertyResult]:
        from apps.config.services import SystemConfigService
        if not SystemConfigService.scraper_enabled():
            return []

        from django.core.cache import cache
        key    = cls._search_cache_key(city, location, area_marla, max_price, property_type)
        cached = cache.get(key)

        if cached is not None:
            logger.debug(f"Scraper cache hit: key={key} ({len(cached)} results)")
            return [PropertyResult.from_dict(d) for d in cached]

        # Cache miss — fire background task; return nothing now
        try:
            from .tasks import warm_search_cache
            warm_search_cache.delay(
                cache_key=key, city=city, location=location,
                area_marla=area_marla, max_price=max_price,
                property_type=property_type, phone=phone,
            )
            logger.info(f"Scraper cache miss — background task queued city={city!r} phone={phone!r}")
        except Exception as exc:
            logger.error(f"Failed to queue warm_search_cache: {exc}")

        return []

    # ── Deduplication ────────────────────────────────────────────────────────

    @classmethod
    def _dedup(cls, results: list[PropertyResult]) -> list[PropertyResult]:
        """
        Remove near-duplicate listings that appear across multiple sources
        (e.g. same property on both Zameen and Graana, or a DB listing that's
        also scraped from an external site).  DB results (source='pakprop')
        come first in the input so they always win when a duplicate is found.
        O(n²) but n ≤ ~25 so cost is negligible.
        """
        kept: list[PropertyResult] = []
        for candidate in results:
            if not any(cls._is_duplicate(candidate, existing) for existing in kept):
                kept.append(candidate)
        return kept

    @staticmethod
    def _is_duplicate(a: PropertyResult, b: PropertyResult) -> bool:
        # City must match (normalised)
        if (a.city or '').lower().strip() != (b.city or '').lower().strip():
            return False

        # Property type must match when both are present
        type_a = (a.property_type or '').lower().strip()
        type_b = (b.property_type or '').lower().strip()
        if type_a and type_b and type_a != type_b:
            return False

        # Area: within 15% when both present
        if a.area_marla and b.area_marla:
            ratio = a.area_marla / b.area_marla
            if not (0.85 <= ratio <= 1.15):
                return False

        # Price: within 25% — scrapers round differently (1.08 crore vs 1.05 crore)
        if a.price and b.price:
            ratio = a.price / b.price
            if not (0.75 <= ratio <= 1.25):
                return False

        # Location: if both non-empty, at least one significant word must overlap
        loc_a = (a.location or '').lower()
        loc_b = (b.location or '').lower()
        if loc_a and loc_b:
            words_a = {w for w in loc_a.split() if len(w) >= 3}
            words_b = {w for w in loc_b.split() if len(w) >= 3}
            if words_a and words_b and not (words_a & words_b):
                return False

        # At this point: same city, compatible type, similar area+price, overlapping
        # location — treat as duplicate; caller keeps the one that arrived first.
        return True

    # ── AI verdicts (batch, cost-aware) ───────────────────────────────────────

    @classmethod
    def _apply_ai_verdicts(cls, results: list[PropertyResult]):
        unscored = [r for r in results if not r.ai_verdict]
        if not unscored:
            return
        try:
            from django.conf import settings
            if getattr(settings, 'AI_BACKEND', 'gemini') == 'local':
                return  # skip Gemini call in local mode — no quota to burn
            from apps.ai.scoring import batch_verdicts
            verdicts = batch_verdicts(unscored)
            for r in unscored:
                r.ai_verdict = verdicts.get(r.source_id, '')
        except Exception as exc:
            logger.warning(f"AI verdict batch failed: {exc}")

    # ── Pagination helper ─────────────────────────────────────────────────────

    @staticmethod
    def format_page(results: list[PropertyResult], offset: int, total: int) -> str:
        end   = min(offset + PAGE_SIZE, total)
        chunk = results[offset:end]

        lines = [f"Showing {offset + 1}–{end} of {total} listing{'s' if total > 1 else ''}:\n"]
        for i, r in enumerate(chunk, start=offset + 1):
            lines.append(r.format_wa(index=i))

        if end < total:
            lines.append(f"\nReply *more* for next {min(PAGE_SIZE, total - end)} listings.")
        else:
            lines.append("\nThat's all the listings. Ask anything else.")

        return "\n\n".join(lines)
