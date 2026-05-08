"""
Unified property search — merges local DB listings with scraped web results.
All WhatsApp property search flows go through this service.
"""
import logging
from .models import Property
from .scrapers.base import PropertyResult
from .scrapers.registry import search_all_scrapers

logger = logging.getLogger(__name__)

PAGE_SIZE = 3  # listings per WhatsApp page


class PropertySearchService:

    @classmethod
    def search(cls, city='', location='', area_marla=None, max_price=None,
               property_type='', furnished_status='',
               construction_status='') -> list[PropertyResult]:
        """
        Returns merged + AI-scored results (DB first, then scraped).
        """
        db_results      = cls._from_db(city, location, area_marla, max_price,
                                       property_type, furnished_status, construction_status)
        scraped_results = cls._from_scrapers(city, location, area_marla, max_price, property_type)

        all_results = db_results + scraped_results
        if not all_results:
            return []

        cls._apply_ai_verdicts(all_results[:5])  # AI score top 5 only (cost-aware)
        return all_results

    # ── Sources ───────────────────────────────────────────────────────────────

    @classmethod
    def _from_db(cls, city, location, area_marla, max_price, property_type,
                 furnished_status='', construction_status='') -> list[PropertyResult]:
        qs = Property.objects.filter(is_active=True)
        if city:
            qs = qs.filter(city__icontains=city)
        if location:
            qs = qs.filter(location__icontains=location)
        if max_price:
            qs = qs.filter(price_pkr__lte=max_price)
        if area_marla:
            qs = qs.filter(area_marla__gte=area_marla * 0.8,
                           area_marla__lte=area_marla * 1.2)
        if property_type:
            qs = qs.filter(property_type__icontains=property_type)
        if furnished_status:
            qs = qs.filter(furnished_status=furnished_status)
        if construction_status:
            qs = qs.filter(construction_status=construction_status)

        return [
            PropertyResult(
                source               = 'pakprop',
                source_id            = str(p.id),
                title                = p.title,
                city                 = p.city,
                location             = p.location,
                area_marla           = float(p.area_marla) if p.area_marla else None,
                price_pkr            = p.price_pkr,
                property_type        = p.property_type,
                furnished_status     = p.furnished_status,
                construction_status  = p.construction_status,
                url                  = '',
                ai_score             = p.ai_score,
            )
            for p in qs.order_by('-ai_score', '-created_at')[:10]
        ]

    @classmethod
    def _from_scrapers(cls, city, location, area_marla, max_price, property_type) -> list[PropertyResult]:
        from apps.config.services import SystemConfigService
        if not SystemConfigService.scraper_enabled():
            return []
        try:
            return search_all_scrapers(
                city=city, location=location, area_marla=area_marla,
                max_price=max_price, property_type=property_type,
            )
        except Exception as exc:
            logger.error(f"Scraper search failed: {exc}")
            return []

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
            from services.ai_orchestrator import AIOrchestrator
            verdicts = AIOrchestrator.batch_verdicts(unscored)
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
