"""
Graana.com scraper.
Update _parse() selectors if Graana changes their HTML.
"""
import logging
import requests
from bs4 import BeautifulSoup
from django.core.cache import cache

from .base import BaseScraper, PropertyResult

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}


class GraanaScraper(BaseScraper):
    site_name = 'graana'
    BASE_URL  = 'https://www.graana.com'
    CACHE_TTL = 3600

    def search(self, city='', location='', area_marla=None,
               max_price=None, property_type='') -> list[PropertyResult]:
        key = f"scraper:graana:{city}:{location}:{area_marla}:{max_price}:{property_type}"
        cached = cache.get(key)
        if cached is not None:
            return [PropertyResult.from_dict(d) for d in cached]

        results = self._fetch(city, location, property_type)

        if max_price:
            results = [r for r in results if not r.price_pkr or r.price_pkr <= max_price]

        cache.set(key, [r.to_dict() for r in results], self.CACHE_TTL)
        return results

    def _fetch(self, city: str, location: str, property_type: str) -> list[PropertyResult]:
        city_slug = city.lower().replace(' ', '-') or 'lahore'
        segments  = ['property-for-sale', city_slug]
        if location:
            segments.append(location.lower().replace(' ', '-'))
        url = f"{self.BASE_URL}/{'/'.join(segments)}/"

        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(f"Graana fetch failed ({url}): {exc}")
            return []

        return self._parse(resp.text, city)

    def _parse(self, html: str, city: str) -> list[PropertyResult]:
        soup    = BeautifulSoup(html, 'lxml')
        results = []

        cards = (
            soup.select('[class*="PropertyCard"]') or
            soup.select('[class*="property-card"]') or
            soup.select('article') or
            soup.select('[class*="listing-item"]')
        )

        for card in cards[:10]:
            try:
                title_el = card.select_one('h2, h3, [class*="title"]')
                price_el = card.select_one('[class*="price"]')
                loc_el   = card.select_one('[class*="location"], [class*="area"]')
                area_el  = card.select_one('[class*="area-size"], [class*="size"]')
                link_el  = card.select_one('a[href]')

                title    = title_el.get_text(strip=True) if title_el else ''
                if not title:
                    continue

                price    = self.parse_pkr(price_el.get_text(strip=True)) if price_el else None
                location = loc_el.get_text(strip=True) if loc_el else city
                area     = self.parse_area(area_el.get_text(strip=True)) if area_el else None
                href     = link_el.get('href', '') if link_el else ''
                url      = (self.BASE_URL + href) if href.startswith('/') else href
                sid      = href.split('/')[-2] if href else title[:20]

                results.append(PropertyResult(
                    source='graana', source_id=f"graana-{sid}",
                    title=title, city=city, location=location,
                    area_marla=area, price_pkr=price,
                    property_type='plot' if 'plot' in title.lower() else 'residential',
                    url=url,
                ))
            except Exception:
                logger.debug("Graana card parse failed", exc_info=True)

        logger.info(f"Graana returned {len(results)} results for {city}")
        return results
