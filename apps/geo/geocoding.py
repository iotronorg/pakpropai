"""
Geocoding of location centroids (T-1060c, ADR-024).

Only ``Location`` nodes are geocoded (city / locality / sub-locality), never
individual listings: a few thousand requests a month instead of one per
listing write. A listing without its own coordinates is placed at its
location's centroid (snapped, approximate) by the public-point trigger.

* ``get_geocoder()`` → ``None`` unless ``GEOCODING_ENABLED`` and a key are set.
* ``GoogleGeocoder``: the Geocoding API with ``components=country:XX`` and a
  timeout; the API key is a query parameter, so request errors are logged by
  type only (``requests`` puts the URL, with the key, in exception messages).
* Daily request budget (``GEOCODING_DAILY_BUDGET``) counted in the cache; a
  cache hit costs nothing. Results are cached ``GEOCODING_CACHE_DAYS`` (≤ 30:
  Google allows coordinates to be cached for at most 30 days).
* ``geocode_location(loc)`` builds the query from the tree ("DHA, Lahore,
  Punjab, Pakistan"), checks the result (country, not coarser than asked, near
  the parent city) and stores the centroid + place id, or a status saying why
  not.
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

GOOGLE_URL = 'https://maps.googleapis.com/maps/api/geocode/json'
CACHE_PREFIX = 'geocode:v1:'
# A locality more than this far from its (geocoded) city is a wrong match.
MAX_KM_FROM_PARENT = {'locality': 60.0, 'sub_locality': 60.0, 'city': 400.0}
# Google result types that are coarser than each level.
_TOO_COARSE = {
    'city':         {'country', 'administrative_area_level_1'},
    'locality':     {'country', 'administrative_area_level_1', 'administrative_area_level_2'},
    'sub_locality': {'country', 'administrative_area_level_1', 'administrative_area_level_2'},
}


class GeocodingError(Exception):
    """Provider unavailable / denied / bad response (retry later)."""


class BudgetExhausted(Exception):
    """The daily request budget is used up."""


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lng: float
    place_id: str
    formatted: str
    types: tuple
    country: str              # ISO 3166-1 alpha-2 from address_components
    partial: bool


class GoogleGeocoder:
    name = 'google'

    def __init__(self, api_key: str, timeout: float = 5.0):
        self._key = api_key
        self._timeout = timeout

    def geocode(self, query: str, *, country: str) -> GeocodeResult | None:
        """First result for ``query`` restricted to ``country``; None = no result."""
        import requests
        try:
            resp = requests.get(GOOGLE_URL, timeout=self._timeout, params={
                'address': query, 'components': f'country:{country}',
                'region': country.lower(), 'key': self._key})
        except requests.RequestException as exc:
            # Never log str(exc): it contains the URL, i.e. the API key.
            raise GeocodingError(f'request failed: {type(exc).__name__}') from None
        if resp.status_code != 200:
            raise GeocodingError(f'HTTP {resp.status_code}')
        try:
            body = resp.json()
        except ValueError:
            raise GeocodingError('invalid JSON') from None
        status = body.get('status')
        if status == 'ZERO_RESULTS':
            return None
        if status != 'OK' or not body.get('results'):
            # REQUEST_DENIED / OVER_QUERY_LIMIT / INVALID_REQUEST …; error_message never has the key.
            raise GeocodingError(f"status {status}: {str(body.get('error_message', ''))[:120]}")
        r = body['results'][0]
        loc = (r.get('geometry') or {}).get('location') or {}
        try:
            lat, lng = float(loc['lat']), float(loc['lng'])
        except (KeyError, TypeError, ValueError):
            raise GeocodingError('result without coordinates') from None
        country_code = next((c.get('short_name', '') for c in r.get('address_components', [])
                             if 'country' in c.get('types', [])), '')
        return GeocodeResult(lat=lat, lng=lng, place_id=str(r.get('place_id', ''))[:300],
                             formatted=str(r.get('formatted_address', ''))[:300],
                             types=tuple(r.get('types', [])), country=country_code.upper(),
                             partial=bool(r.get('partial_match')))


def get_geocoder():
    if not getattr(settings, 'GEOCODING_ENABLED', False):
        return None
    key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
    if not key:
        return None
    return GoogleGeocoder(key, timeout=settings.GEOCODING_TIMEOUT_S)


# ── Budget + cache ────────────────────────────────────────────────────────────

def _budget_key() -> str:
    return f'geocode:budget:{timezone.now():%Y%m%d}'


def budget_used() -> int:
    return int(cache.get(_budget_key()) or 0)


def _spend_one():
    key = _budget_key()
    cache.add(key, 0, timeout=2 * 86400)
    used = cache.incr(key)
    if used > settings.GEOCODING_DAILY_BUDGET:
        raise BudgetExhausted(f'daily geocoding budget of {settings.GEOCODING_DAILY_BUDGET} reached')


def _cache_key(query: str, country: str) -> str:
    return CACHE_PREFIX + hashlib.sha256(f'{country}|{query.lower()}'.encode()).hexdigest()


def cached_geocode(geocoder, query: str, *, country: str) -> GeocodeResult | None:
    key = _cache_key(query, country)
    hit = cache.get(key)
    if hit is not None:
        return GeocodeResult(**hit) if hit else None          # {} = cached "no result"
    _spend_one()
    result = geocoder.geocode(query, country=country)
    ttl = min(30, settings.GEOCODING_CACHE_DAYS) * 86400
    cache.set(key, result.__dict__ if result else {}, timeout=ttl if result else ttl // 4)
    return result


# ── Location centroids ────────────────────────────────────────────────────────

def _ancestors(loc):
    """Ancestors nearest first, freshly loaded (one query; ``path`` prefixes)."""
    from apps.locations.models import Location
    parts = loc.path.split('/')
    prefixes = ['/'.join(parts[:i]) for i in range(1, len(parts))]
    return list(Location.objects.filter(path__in=prefixes).order_by('-path')
                .only('name', 'level', 'latitude', 'longitude', 'path'))


def query_for(loc) -> str:
    """'DHA Phase 5, DHA, Lahore, Punjab, Pakistan' (self + ancestors, nearest first)."""
    names = []
    for name in [loc.name] + [a.name for a in _ancestors(loc)]:
        if not names or names[-1].lower() != name.lower():     # "Dubai, Dubai" → "Dubai"
            names.append(name)
    return ', '.join(names)


def _km(lat1, lng1, lat2, lng2) -> float:
    r = math.radians
    a = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lng2 - lng1) / 2) ** 2)
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def _nearest_geocoded_ancestor(loc):
    return next((a for a in _ancestors(loc) if a.level != 'country'
                 and a.latitude is not None and a.longitude is not None), None)


def check_result(loc, result: GeocodeResult) -> str | None:
    """Why the result is unusable for ``loc`` (None = fine)."""
    if result.country != loc.market_id:
        return f'country {result.country or "?"} is not {loc.market_id}'
    coarse = _TOO_COARSE.get(loc.level, set())
    kinds = set(result.types) - {'political'}          # generic tag on most results
    if kinds and kinds <= coarse:
        return f'too coarse: {",".join(result.types)}'
    anc = _nearest_geocoded_ancestor(loc)
    if anc is not None:
        d = _km(float(anc.latitude), float(anc.longitude), result.lat, result.lng)
        limit = MAX_KM_FROM_PARENT.get(loc.level, 400.0)
        if d > limit:
            return f'{d:.0f} km from {anc.name} (> {limit:.0f})'
    return None


def geocode_location(loc, geocoder) -> str:
    """Geocode one node and save its status. Returns the status. Raises BudgetExhausted."""
    from decimal import Decimal
    from .search import validate_point
    Status = type(loc).GeocodeStatus
    note = ''
    try:
        result = cached_geocode(geocoder, query_for(loc), country=loc.market_id)
    except GeocodingError as exc:
        logger.warning('geocoding %s failed: %s', loc.path, exc)
        status, note = Status.ERROR, str(exc)[:200]
        result = None
    else:
        if result is None:
            status = Status.ZERO
        elif result.partial:
            status, note = Status.PARTIAL, result.formatted[:200]
        elif (why := check_result(loc, result)):
            status, note = Status.REJECTED, why[:200]
        else:
            validate_point(result.lat, result.lng)
            status, note = Status.OK, result.formatted[:200]
    fields = ['geocode_status', 'geocoded_at', 'geocode_note']
    loc.geocode_status, loc.geocoded_at, loc.geocode_note = status, timezone.now(), note
    if status == Status.OK:
        loc.latitude = Decimal(f'{result.lat:.6f}')
        loc.longitude = Decimal(f'{result.lng:.6f}')
        loc.geocode_place_id = result.place_id
        fields += ['latitude', 'longitude', 'geocode_place_id']
    elif result is not None and result.place_id:
        loc.geocode_place_id = result.place_id
        fields.append('geocode_place_id')
    loc.save(update_fields=fields)
    if status == Status.OK:
        refresh_listing_points(loc)
    return status


def refresh_listing_points(loc) -> int:
    """Re-fire the public-point trigger for listings that rely on this centroid."""
    from apps.properties.models import Property
    return (Property.objects.filter(location_ref=loc, latitude__isnull=True)
            .update(location_ref=loc))


def due_locations(market: str | None = None, retry_failed: bool = False):
    """Nodes to (re-)geocode: pending, or OK older than GEOCODING_REFRESH_DAYS (Google 30-day rule)."""
    from datetime import timedelta
    from django.db.models import Q
    from apps.locations.models import Location
    S = Location.GeocodeStatus
    stale = timezone.now() - timedelta(days=settings.GEOCODING_REFRESH_DAYS)
    q = Q(geocode_status=S.PENDING) | Q(geocode_status=S.OK, geocoded_at__lt=stale)
    if retry_failed:
        q |= Q(geocode_status__in=[S.ERROR, S.ZERO, S.REJECTED])
    else:
        q |= Q(geocode_status=S.ERROR, geocoded_at__lt=timezone.now() - timedelta(days=1))
    qs = Location.objects.filter(q, is_active=True, level__in=['city', 'locality', 'sub_locality'])
    if market:
        qs = qs.filter(market_id=market.upper())
    else:
        qs = qs.filter(market__status='active')       # launched markets only (budget)
    # Cities first: their centroids anchor the distance check for localities.
    return qs.order_by('path')


def run(limit: int = 200, market: str | None = None, retry_failed: bool = False) -> dict:
    """Geocode due locations. Returns counts per status; stops at the budget."""
    geocoder = get_geocoder()
    stats = {'disabled': geocoder is None, 'budget_exhausted': False}
    if geocoder is None:
        return stats
    for loc in due_locations(market, retry_failed)[:limit]:
        try:
            status = geocode_location(loc, geocoder)
        except BudgetExhausted:
            stats['budget_exhausted'] = True
            break
        stats[status] = stats.get(status, 0) + 1
    return stats
