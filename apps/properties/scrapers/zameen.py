"""
Zameen.com scraper.

Selectors are maintained here — update _parse_card() if Zameen changes their HTML.
Results are cached in Redis for 1 hour to avoid hammering the site.

Confirmed working selectors (verified 2026-05-06):
  card     : article._5b98ebdf
  title    : a[aria-label="Listing link"] → title attribute
  price    : h4._0e3d05b8  e.g. "PKR1.08 Crore"
  location : div.db1aca2f
  area     : div.af969661  e.g. "5 Marla"
  link     : a[aria-label="Listing link"] → href attribute
"""
import logging
import requests
from bs4 import BeautifulSoup
from django.core.cache import cache

from .base import BaseScraper, PropertyResult

logger = logging.getLogger(__name__)

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
        key = f"scraper_zameen_{city}_{location}_{area_marla}_{max_price}_{property_type}"
        cached = cache.get(key)
        if cached is not None:
            return [PropertyResult.from_dict(d) for d in cached]

        results = self._fetch(city, property_type)

        if location:
            loc = location.lower()
            results = [r for r in results if loc in (r.location or '').lower()]
        if max_price:
            results = [r for r in results if not r.price_pkr or r.price_pkr <= max_price]
        if area_marla:
            results = [r for r in results
                       if not r.area_marla or abs(r.area_marla - area_marla) / area_marla < 0.4]

        cache.set(key, [r.to_dict() for r in results], self.CACHE_TTL)
        return results

    def _fetch(self, city: str, property_type: str) -> list[PropertyResult]:
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

        city_name = city_slug.split('-')[0]
        return self._parse(resp.text, city_name)

    def _parse(self, html: str, city: str) -> list[PropertyResult]:
        soup    = BeautifulSoup(html, 'lxml')
        results = []

        cards = soup.select('article._5b98ebdf')
        logger.debug(f"Zameen: found {len(cards)} cards")

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
        link_el  = card.select_one('a[aria-label="Listing link"]')
        price_el = card.select_one('h4._0e3d05b8')
        loc_el   = card.select_one('div.db1aca2f')
        area_el  = card.select_one('div.af969661')

        if not link_el:
            return None

        title = link_el.get('title', '').strip()
        if not title:
            return None

        href      = link_el.get('href', '')
        url       = (self.BASE_URL + href) if href.startswith('/') else href
        source_id = href.rstrip('/').split('/')[-1] or title[:30]

        price    = self.parse_pkr(price_el.get_text(strip=True)) if price_el else None
        location = loc_el.get_text(strip=True) if loc_el else city
        area     = self.parse_area(area_el.get_text(strip=True)) if area_el else None

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
