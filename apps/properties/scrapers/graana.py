"""
Graana.com scraper.

Update _parse() selectors if Graana changes their HTML.

Confirmed working selectors (verified 2026-05-06):
  card     : div.mui-style-qwav6q
  link     : a[href*="/property/"]
  price    : [class*="h4New"]       e.g. "93 Lac", "1.8 Crore"
  location : h5                     e.g. "Bahria Enclave,Islamabad"
  type+area: [class*="body2New"]    [0] = "residential Plot", [1] = "5marla"

URL format: https://www.graana.com/sale/?type={type}&city={city}
"""
import logging
import requests
from bs4 import BeautifulSoup
from django.core.cache import cache

from .base import BaseScraper, PropertyResult

logger = logging.getLogger(__name__)

_TYPE_SLUGS = {
    'plot':        'plot',
    'house':       'house',
    'apartment':   'apartment',
    'flat':        'apartment',
    'commercial':  'commercial',
    'residential': 'residential',
}

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
        key = self.safe_cache_key(
            'scraper_graana', city=city, location=location,
            area=area_marla, price=max_price, ptype=property_type,
        )
        cached = cache.get(key)
        if cached is not None:
            return [PropertyResult.from_dict(d) for d in cached]

        results = self._fetch(city, property_type)

        if location:
            results = [r for r in results if self.location_matches(r.location, location)]
        if max_price:
            results = [r for r in results if not r.price_pkr or r.price_pkr <= max_price]
        if area_marla:
            results = [r for r in results
                       if not r.area_marla or abs(r.area_marla - area_marla) / area_marla < 0.4]

        cache.set(key, [r.to_dict() for r in results], self.CACHE_TTL)
        return results

    def _fetch(self, city: str, property_type: str) -> list[PropertyResult]:
        type_slug = _TYPE_SLUGS.get(property_type.lower(), 'residential')
        params    = [f"type={type_slug}"]
        if city:
            params.append(f"city={city.lower().replace(' ', '-')}")
        url = f"{self.BASE_URL}/sale/?{'&'.join(params)}"

        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(f"Graana fetch failed ({url}): {exc}")
            return []

        return self._parse(resp.text, city or 'Pakistan')

    def _parse(self, html: str, city: str) -> list[PropertyResult]:
        soup    = BeautifulSoup(html, 'lxml')
        results = []

        cards = soup.select('div.mui-style-qwav6q')
        logger.debug(f"Graana: found {len(cards)} cards")

        for card in cards[:10]:
            try:
                result = self._parse_card(card, city)
                if result:
                    results.append(result)
            except Exception:
                logger.debug("Graana card parse failed", exc_info=True)

        logger.info(f"Graana returned {len(results)} results for {city}")
        return results

    def _parse_card(self, card, city: str) -> PropertyResult | None:
        link_el  = card.select_one('a[href*="/property/"]')
        price_el = card.select_one('[class*="h4New"]')
        loc_el   = card.select_one('h5')
        body2s   = card.select('[class*="body2New"]')

        if not link_el:
            return None

        href = link_el.get('href', '')
        url  = (self.BASE_URL + href) if href.startswith('/') else href
        sid  = href.rstrip('/').split('/')[-1] or href[:30]

        price    = self.parse_pkr(price_el.get_text(strip=True)) if price_el else None
        location = loc_el.get_text(strip=True) if loc_el else city

        prop_type_text = body2s[0].get_text(strip=True) if len(body2s) > 0 else ''
        area_text      = body2s[1].get_text(strip=True) if len(body2s) > 1 else ''
        area           = self.parse_area(area_text) if area_text else None

        # Construct a human-readable title from available fields
        title_parts = [p for p in [area_text, prop_type_text, f"in {location}"] if p]
        title       = ' '.join(title_parts) or location
        if not title:
            return None

        prop_type = 'plot' if 'plot' in prop_type_text.lower() else 'residential'

        return PropertyResult(
            source        = 'graana',
            source_id     = f"graana-{sid}",
            title         = title,
            city          = city,
            location      = location,
            area_marla    = area,
            price_pkr     = price,
            property_type = prop_type,
            url           = url,
        )
