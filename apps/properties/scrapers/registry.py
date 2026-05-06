"""
Scraper registry — add new scrapers here.

To add a new site:
1. Create apps/properties/scrapers/<site>.py (subclass BaseScraper)
2. Import and append the class to SCRAPERS below
3. Set ENABLED = True on the class
"""
import concurrent.futures
import logging

from .base import PropertyResult
from .zameen import ZameenScraper
from .graana import GraanaScraper

logger = logging.getLogger(__name__)

# ── Add new scrapers here ──────────────────────────────────────────────────────
SCRAPERS = [
    ZameenScraper,
    GraanaScraper,
]
# ──────────────────────────────────────────────────────────────────────────────

_TIMEOUT = 20  # seconds total for all scrapers to respond


def search_all_scrapers(city='', location='', area_marla=None,
                        max_price=None, property_type='') -> list[PropertyResult]:
    """Run all enabled scrapers in parallel, aggregate results."""
    enabled = [cls() for cls in SCRAPERS if cls.ENABLED]
    if not enabled:
        return []

    kwargs  = dict(city=city, location=location, area_marla=area_marla,
                   max_price=max_price, property_type=property_type)
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(enabled)) as pool:
        futures = {pool.submit(s.search, **kwargs): s.site_name for s in enabled}
        done, _ = concurrent.futures.wait(futures, timeout=_TIMEOUT)
        for future in done:
            site = futures[future]
            try:
                results.extend(future.result())
            except Exception as exc:
                logger.warning(f"Scraper [{site}] failed: {exc}")

    return results
