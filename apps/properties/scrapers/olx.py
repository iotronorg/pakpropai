"""
OLX Pakistan scraper (olx.com.pk).
Update _parse() selectors if OLX changes their HTML.
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

_CITY_SLUGS = {
    'lahore':     'lahore',
    'karachi':    'karachi',
    'islamabad':  'islamabad',
    'rawalpindi': 'rawalpindi',
    'faisalabad': 'faisalabad',
    'multan':     'multan',
    'peshawar':   'peshawar',
    'quetta':     'quetta',
}

# OLX property keywords — filter non-property ads
_PROPERTY_KEYWORDS = {'plot', 'house', 'flat', 'apartment', 'marla', 'kanal',
                      'property', 'villa', 'commercial', 'shop', 'plaza'}


class OLXScraper(BaseScraper):
    site_name = 'olx'
    BASE_URL  = 'https://www.olx.com.pk'
    CACHE_TTL = 3600

    def search(self, city='', location='', area_marla=None,
               max_price=None, property_type='') -> list[PropertyResult]:
        key = f"scraper:olx:{city}:{location}:{area_marla}:{max_price}:{property_type}"
        cached = cache.get(key)
        if cached is not None:
            return [PropertyResult.from_dict(d) for d in cached]

        results = self._fetch(city, location, property_type)

        if max_price:
            results = [r for r in results if not r.price_pkr or r.price_pkr <= max_price]

        cache.set(key, [r.to_dict() for r in results], self.CACHE_TTL)
        return results

    def _fetch(self, city: str, location: str, property_type: str) -> list[PropertyResult]:
        city_slug = _CITY_SLUGS.get(city.lower().split()[0], 'lahore')
        query     = location or property_type or 'property'
        url       = f"{self.BASE_URL}/items/{city_slug}/q-{query.replace(' ', '-')}"

        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(f"OLX fetch failed ({url}): {exc}")
            return []

        return self._parse(resp.text, city)

    def _parse(self, html: str, city: str) -> list[PropertyResult]:
        soup    = BeautifulSoup(html, 'lxml')
        results = []

        cards = (
            soup.select('[data-aut-id="itemBox"]') or
            soup.select('li[data-aut-id]') or
            soup.select('[class*="EIR5N"]') or
            soup.select('article')
        )

        for card in cards[:15]:
            try:
                title_el = card.select_one('[data-aut-id="itemTitle"], h2, h3')
                price_el = card.select_one('[data-aut-id="itemPrice"], [class*="price"]')
                link_el  = card.select_one('a[href]')

                title = title_el.get_text(strip=True) if title_el else ''
                if not title:
                    continue

                # Only include property-related ads
                if not any(kw in title.lower() for kw in _PROPERTY_KEYWORDS):
                    continue

                price = self.parse_pkr(price_el.get_text(strip=True)) if price_el else None
                href  = link_el.get('href', '') if link_el else ''
                url   = href if href.startswith('http') else self.BASE_URL + href
                sid   = url.split('/')[-2] if href else title[:20]
                area  = self.parse_area(title)  # OLX often includes size in title

                results.append(PropertyResult(
                    source='olx', source_id=f"olx-{sid}",
                    title=title, city=city, location=city,
                    area_marla=area, price_pkr=price,
                    property_type='plot' if 'plot' in title.lower() else 'residential',
                    url=url,
                ))
            except Exception:
                logger.debug("OLX card parse failed", exc_info=True)

        logger.info(f"OLX returned {len(results)} results for {city}")
        return results
