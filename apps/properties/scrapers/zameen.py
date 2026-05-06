"""
Zameen.com scraper.

Selectors are maintained here — update _parse_card() if Zameen changes their HTML.
Results are cached in Redis for 1 hour to avoid hammering the site.
"""
import logging
import requests
from bs4 import BeautifulSoup
from django.core.cache import cache

from .base import BaseScraper, PropertyResult

logger = logging.getLogger(__name__)

# Zameen city slug → URL segment
_CITY_SLUGS = {
    'lahore':     'Lahore-2',
    'karachi':    'Karachi-1',
    'islamabad':  'Islamabad-3',
    'rawalpindi': 'Rawalpindi-41',
    'faisalabad': 'Faisalabad-5',
    'multan':     'Multan-9',
    'peshawar':   'Peshawar-6',
    'quetta':     'Quetta-7',
    'sialkot':    'Sialkot-11',
}

_TYPE_SLUGS = {
    'plot':       'Plots',
    'house':      'Homes',
    'apartment':  'Apartments',
    'flat':       'Apartments',
    'commercial': 'Commercial',
}

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


class ZameenScraper(BaseScraper):
    site_name = 'zameen'
    BASE_URL  = 'https://www.zameen.com'
    CACHE_TTL = 3600  # 1 hour

    def search(self, city='', location='', area_marla=None,
               max_price=None, property_type='') -> list[PropertyResult]:
        key = f"scraper:zameen:{city}:{location}:{area_marla}:{max_price}:{property_type}"
        cached = cache.get(key)
        if cached is not None:
            return [PropertyResult.from_dict(d) for d in cached]

        results = self._fetch(city, location, property_type)

        # Client-side filtering (price + area) since URL filters are limited
        if max_price:
            results = [r for r in results if not r.price_pkr or r.price_pkr <= max_price]
        if area_marla:
            results = [r for r in results
                       if not r.area_marla or abs(r.area_marla - area_marla) / area_marla < 0.4]

        cache.set(key, [r.to_dict() for r in results], self.CACHE_TTL)
        return results

    def _fetch(self, city: str, location: str, property_type: str) -> list[PropertyResult]:
        city_key  = city.lower().split()[0] if city else 'lahore'
        city_slug = _CITY_SLUGS.get(city_key, 'Lahore-2')
        type_slug = _TYPE_SLUGS.get(property_type.lower(), 'Homes')
        url       = f"{self.BASE_URL}/{type_slug}/{city_slug}-1.html"

        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(f"Zameen fetch failed ({url}): {exc}")
            return []

        return self._parse(resp.text, city_slug.split('-')[0])

    def _parse(self, html: str, city: str) -> list[PropertyResult]:
        soup    = BeautifulSoup(html, 'lxml')
        results = []

        # Zameen has changed class names over time — try multiple selectors
        cards = (
            soup.select('li[class*="listing"]') or
            soup.select('article[class*="property"]') or
            soup.select('[data-listing-id]') or
            soup.select('li.ef447dde')
        )

        for card in cards[:10]:
            try:
                result = self._parse_card(card, city)
                if result:
                    results.append(result)
            except Exception:
                logger.debug("Zameen card parse failed", exc_info=True)

        logger.info(f"Zameen returned {len(results)} results for {city}")
        return results

    def _parse_card(self, card, city: str) -> PropertyResult | None:
        title_el  = card.select_one('h2, h3, [class*="title"]')
        price_el  = card.select_one('[class*="price"]')
        loc_el    = card.select_one('[class*="location"], [class*="area-location"]')
        area_el   = card.select_one('[aria-label*="area"], [class*="area-size"], [class*="bed"]')
        link_el   = card.select_one('a[href]')

        title     = title_el.get_text(strip=True) if title_el else ''
        if not title:
            return None

        price     = self.parse_pkr(price_el.get_text(strip=True)) if price_el else None
        location  = loc_el.get_text(strip=True) if loc_el else city
        area      = self.parse_area(area_el.get_text(strip=True)) if area_el else None

        href      = link_el.get('href', '') if link_el else ''
        url       = (self.BASE_URL + href) if href.startswith('/') else href
        source_id = card.get('data-listing-id') or url.split('/')[-2] or title[:20]

        return PropertyResult(
            source        = 'zameen',
            source_id     = f"zameen-{source_id}",
            title         = title,
            city          = city,
            location      = location,
            area_marla    = area,
            price_pkr     = price,
            property_type = 'plot' if 'plot' in title.lower() else 'residential',
            url           = url,
        )
