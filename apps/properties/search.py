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
from .scrapers.base import PropertyResult, BaseScraper

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
    purpose: str = ''                # 'sale' | 'rent' | '' — reserved for future filter
    min_price: int = 0
    max_price: int = 0
    area_marla: float = 0.0          # target size; DB query uses ±20 % tolerance band
    furnished_status: str = ''
    construction_status: str = ''
    installments: bool = False       # True → only installment-available listings
    legal_status: str = ''           # '' | 'verified' | 'pending' | ...
    organization_id: str = ''        # restrict results to one org's inventory
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
            min_price=int(params.get('min_price', 0) or 0),
            max_price=int(
                params.get('max_price_pkr', 0) or params.get('max_price', 0) or 0
            ),
            area_marla=float(params.get('area_marla', 0) or 0),
            furnished_status=str(
                params.get('furnished', '') or params.get('furnished_status', '')
            ),
            construction_status=str(params.get('construction_status', '')),
            installments=bool(params.get('installments', False)),
            legal_status=str(params.get('legal_status', '')),
            organization_id=str(params.get('organization_id', '')),
            page=int(params.get('page', 0) or 0),
            page_size=int(params.get('page_size', PAGE_SIZE) or PAGE_SIZE),
            phone=str(params.get('phone', '')),
        )


class PropertySearchService:

    @classmethod
    def execute(cls, params: SearchParams) -> dict:
        """
        Primary entry point for the AI layer.
        Accepts a SearchParams object and returns a structured payload
        ready for the WhatsApp formatter or API layer.
        """
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
        )
        scraped_results = cls._from_scrapers(
            city=params.city,
            location=params.location,
            area_marla=params.area_marla or None,
            max_price=params.max_price or None,
            property_type=params.property_type,
            phone=params.phone,
        )

        all_results = cls._dedup(db_results + scraped_results)
        if all_results:
            cls._apply_ai_verdicts(all_results[:5])

        total = len(all_results)
        offset = params.page * params.page_size
        page_slice = all_results[offset: offset + params.page_size]

        return {
            'query': {
                'city': params.city,
                'location': params.location,
                'property_type': params.property_type,
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
            'live_search_pending': total == 0 or all(r.source == 'pakprop' for r in all_results),
            'whatsapp_message': (
                cls.format_page(all_results, offset, total)
                if all_results
                else (
                    'No properties found right now. Live results from Zameen/Graana are '
                    'being fetched — you will receive them in a follow-up message shortly.'
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
            'price': r.price_pkr,
            'price_formatted': f"PKR {r.price_pkr:,}" if r.price_pkr else 'Price not listed',
            'property_type': r.property_type,
            'furnished_status': r.furnished_status,
            'construction_status': r.construction_status,
            'url': r.url or '',
            'ai_score': r.ai_score,
            'ai_verdict': getattr(r, 'ai_verdict', ''),
        }

    @classmethod
    def search(cls, city='', location='', area_marla=None, max_price=None,
               property_type='', furnished_status='',
               construction_status='', phone: str = '') -> list[PropertyResult]:
        """
        Returns merged + AI-scored results (DB first, then scraped).
        `phone` is forwarded to the background task so a follow-up WA message
        can be sent when live scraper results arrive.
        """
        db_results = cls._from_db(
            city=city, location=location, area_marla=area_marla,
            max_price=max_price, property_type=property_type,
            furnished_status=furnished_status, construction_status=construction_status,
        )
        scraped_results = cls._from_scrapers(
            city=city, location=location, area_marla=area_marla,
            max_price=max_price, property_type=property_type, phone=phone,
        )

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
    ) -> list[PropertyResult]:
        qs = (
            Property.objects
            .filter(is_active=True)
            .select_related('organization', 'listed_by_agent')
            .only(
                'id', 'title', 'city', 'location', 'area_marla', 'price_pkr',
                'property_type', 'furnished_status', 'construction_status',
                'ai_score', 'installment_available', 'legal_status',
            )
        )

        if city:
            qs = qs.filter(city__icontains=city)
        if location:
            qs = qs.filter(location__icontains=location)
        if min_price:
            qs = qs.filter(price_pkr__gte=min_price)
        if max_price:
            qs = qs.filter(price_pkr__lte=max_price)
        if area_marla:
            qs = qs.filter(
                area_marla__gte=area_marla * 0.8,
                area_marla__lte=area_marla * 1.2,
            )
        if property_type:
            qs = qs.filter(property_type__icontains=property_type)
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

        return [
            PropertyResult(
                source              = 'pakprop',
                source_id           = str(p.id),
                title               = p.title,
                city                = p.city,
                location            = p.location,
                area_marla          = float(p.area_marla) if p.area_marla else None,
                price_pkr           = p.price_pkr,
                property_type       = p.property_type,
                furnished_status    = p.furnished_status,
                construction_status = p.construction_status,
                url                 = '',
                ai_score            = p.ai_score,
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
        if a.price_pkr and b.price_pkr:
            ratio = a.price_pkr / b.price_pkr
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
