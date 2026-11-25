"""
Radius / bounding-box search on the listing public point (T-1060b, ADR-024).

Every query runs against ``geo_lat``/``geo_lng`` (the trigger-maintained public
point), never the exact ``latitude``/``longitude``.

Two backends, same results (to within float rounding):

* **plain SQL** (default, any Postgres): a bounding box on the btree
  ``prop_geo_point_idx`` narrows the rows, then the haversine distance is
  computed and filtered in SQL.
* **PostGIS** (``GEO_POSTGIS_ENABLED`` and the extension installed by
  ``manage.py geo_setup``): ``ST_DWithin`` / ``ST_Distance`` on a geography
  expression, served by the GiST index ``prop_geo_point_gist``.
"""
from __future__ import annotations

import math
import time

from django.conf import settings
from django.db import connection
from django.db.models import BooleanField, F, FloatField, Value
from django.db.models.expressions import RawSQL
from django.db.models.functions import ASin, Cast, Cos, Power, Radians, Sin, Sqrt

from .sql import GEOG_EXPR

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEG_LAT = 111.32

_ready_cache: dict = {'at': float('-inf'), 'value': False}   # not 0.0: monotonic() may start near 0
_READY_TTL = 300.0


def postgis_ready() -> bool:
    """Flag on and the extension installed (checked at most every 5 minutes per process)."""
    if not getattr(settings, 'GEO_POSTGIS_ENABLED', False):
        return False
    now = time.monotonic()
    if now - _ready_cache['at'] > _READY_TTL:
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_extension WHERE extname = 'postgis'")
            _ready_cache['value'] = cur.fetchone()[0] == 1
        _ready_cache['at'] = now
    return _ready_cache['value']


def reset_ready_cache():
    _ready_cache['at'] = float('-inf')


def validate_point(lat, lng) -> tuple[float, float]:
    lat, lng = float(lat), float(lng)
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0) or \
            math.isnan(lat) or math.isnan(lng):
        raise ValueError('Latitude must be within ±90 and longitude within ±180.')
    return lat, lng


def bbox_for_radius(lat: float, lng: float, km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lng, max_lng) enclosing the circle; longitude span clamped."""
    dlat = km / KM_PER_DEG_LAT
    cos_lat = math.cos(math.radians(lat))
    dlng = 180.0 if cos_lat < 1e-6 else min(180.0, km / (KM_PER_DEG_LAT * cos_lat))
    return (max(-90.0, lat - dlat), min(90.0, lat + dlat),
            max(-180.0, lng - dlng), min(180.0, lng + dlng))


def _haversine_km(lat: float, lng: float):
    lat2 = Radians(Cast(F('geo_lat'), FloatField()))
    lng2 = Radians(Cast(F('geo_lng'), FloatField()))
    lat1, lng1 = math.radians(lat), math.radians(lng)
    a = (Power(Sin((lat2 - Value(lat1)) / 2), 2)
         + Value(math.cos(lat1)) * Cos(lat2) * Power(Sin((lng2 - Value(lng1)) / 2), 2))
    return Value(2 * EARTH_RADIUS_KM) * ASin(Sqrt(a))


def _point_sql():
    return 'ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography'


def within_radius(qs, lat, lng, km: float, *, annotate: str = 'distance_km'):
    """Listings whose public point is within ``km`` of (lat, lng), annotated with the distance."""
    lat, lng = validate_point(lat, lng)
    min_lat, max_lat, min_lng, max_lng = bbox_for_radius(lat, lng, km)
    qs = qs.filter(geo_lat__gte=min_lat, geo_lat__lte=max_lat,
                   geo_lng__gte=min_lng, geo_lng__lte=max_lng)
    if postgis_ready():
        qs = qs.filter(RawSQL(f'ST_DWithin({GEOG_EXPR}, {_point_sql()}, %s)',
                              (lng, lat, km * 1000.0), output_field=BooleanField()))
        return qs.annotate(**{annotate: RawSQL(f'ST_Distance({GEOG_EXPR}, {_point_sql()}) / 1000.0',
                                               (lng, lat), output_field=FloatField())})
    return qs.annotate(**{annotate: _haversine_km(lat, lng)}).filter(**{f'{annotate}__lte': km})


def within_bbox(qs, min_lng, min_lat, max_lng, max_lat):
    """Listings whose public point is inside the box (no antimeridian wrap)."""
    min_lat, min_lng = validate_point(min_lat, min_lng)
    max_lat, max_lng = validate_point(max_lat, max_lng)
    if min_lat > max_lat or min_lng > max_lng:
        raise ValueError('bbox must be min_lng,min_lat,max_lng,max_lat with min <= max.')
    return qs.filter(geo_lat__gte=min_lat, geo_lat__lte=max_lat,
                     geo_lng__gte=min_lng, geo_lng__lte=max_lng)
