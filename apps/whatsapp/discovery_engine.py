import logging
import math

import requests as _http

logger = logging.getLogger(__name__)

_DIRECTORY_SCORE_WEIGHT = 5


class DirectoryEntryHandler:
    """
    Parses Meta Business Directory click-to-chat referral signals from
    an inbound webhook message payload.

    Call once per inbound message — exits immediately when no referral is present.
    """

    @classmethod
    def handle(cls, message_data: dict, session, lead) -> None:
        referral = message_data.get('referral')
        if not referral:
            return

        source_type = referral.get('source_type', '')
        ctwa_clid   = referral.get('ctwa_clid', '')
        source_url  = referral.get('source_url', '')
        source_id   = referral.get('source_id', '')

        session.context = {**session.context, 'discovery_source': source_type}
        session.save(update_fields=['context'])

        signals = {**lead.intent_signals}
        if ctwa_clid:
            signals['ctwa_clid'] = ctwa_clid
        if source_url:
            signals['discovery_source_url'] = source_url
        if source_id:
            signals['discovery_source_id'] = source_id

        lead.intent_signals = signals
        lead.score          = lead.score + _DIRECTORY_SCORE_WEIGHT
        lead.save(update_fields=['intent_signals', 'score'])

        logger.info(
            "DirectoryEntry: source_type=%s ctwa_clid=%s lead=%s",
            source_type, ctwa_clid, lead.pk,
        )


_NOMINATIM_URL    = 'https://nominatim.openstreetmap.org/reverse'
_NOMINATIM_UA     = 'RealTronAI/1.0 (realtron.ai)'
_HAVERSINE_RADIUS = 25.0   # km
_GEO_TIMEOUT      = 3      # seconds


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


class GeoContextResolver:
    """
    Resolves a dropped location pin to a ranked Property QuerySet.

    Resolution tiers (stops at first hit):
      1. Nominatim reverse-geocode → Property.city ILIKE
      2. Haversine radius <= 25 km against Property.latitude/longitude
      3. NeighbourhoodZone centroid radius membership → zone.city match
    """

    @classmethod
    def resolve(cls, lat: float, lon: float, org, lead, session):
        from apps.properties.models import Property

        city    = None
        tier    = None
        results = Property.objects.none()

        # ── Tier 1: Nominatim ────────────────────────────────────────────
        try:
            nominatim_city = cls._nominatim_lookup(lat, lon)
        except Exception:
            logger.warning("Nominatim lookup raised in resolve() — falling to Tier 2")
            nominatim_city = None
        if nominatim_city:
            qs = Property.objects.filter(
                organization=org, is_active=True,
                city__icontains=nominatim_city,
            )
            if qs.exists():
                city    = nominatim_city
                tier    = 1
                results = qs

        # ── Tier 2: Haversine ────────────────────────────────────────────
        if tier is None:
            candidates = list(
                Property.objects.filter(
                    organization=org, is_active=True,
                    latitude__isnull=False, longitude__isnull=False,
                )
            )
            nearby = [
                p for p in candidates
                if _haversine_km(lat, lon, float(p.latitude), float(p.longitude))
                <= _HAVERSINE_RADIUS
            ]
            if nearby:
                nearby.sort(
                    key=lambda p: _haversine_km(lat, lon, float(p.latitude), float(p.longitude))
                )
                tier    = 2
                city    = nearby[0].city
                results = Property.objects.filter(pk__in=[p.pk for p in nearby])

        # ── Tier 3: NeighbourhoodZone ────────────────────────────────────
        if tier is None:
            from apps.whatsapp.models import NeighbourhoodZone
            for zone in NeighbourhoodZone.objects.filter(organization=org):
                dist = _haversine_km(
                    lat, lon, float(zone.centroid_lat), float(zone.centroid_lon)
                )
                if dist <= float(zone.radius_km):
                    qs = Property.objects.filter(
                        organization=org, is_active=True,
                        city__icontains=zone.city,
                    )
                    if qs.exists():
                        tier    = 3
                        city    = zone.city
                        results = qs
                        break

        # ── Side effects ─────────────────────────────────────────────────
        from decimal import Decimal
        lead.last_known_lat = Decimal(str(lat))
        lead.last_known_lon = Decimal(str(lon))
        if city:
            lead.city_interest = city
        lead.save(update_fields=['last_known_lat', 'last_known_lon', 'city_interest'])

        session.context = {
            **session.context,
            'geo': {'lat': lat, 'lon': lon, 'city': city, 'tier': tier},
        }
        session.save(update_fields=['context'])

        logger.info(
            "GeoResolver: lat=%.4f lon=%.4f city=%s tier=%s results=%d lead=%s",
            lat, lon, city, tier, results.count(), lead.pk,
        )
        return results

    @classmethod
    def _nominatim_lookup(cls, lat: float, lon: float) -> str | None:
        try:
            resp = _http.get(
                _NOMINATIM_URL,
                params={'lat': lat, 'lon': lon, 'format': 'json'},
                headers={'User-Agent': _NOMINATIM_UA},
                timeout=_GEO_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            addr = resp.json().get('address', {})
            return (
                addr.get('city')
                or addr.get('town')
                or addr.get('suburb')
                or addr.get('county')
            )
        except Exception:
            logger.warning("Nominatim lookup failed for lat=%s lon=%s", lat, lon, exc_info=True)
            return None
