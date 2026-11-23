"""Link listings to the hierarchy (T-1060a): API writes, the command and the hourly task."""
import logging

from .services import match_location

logger = logging.getLogger(__name__)


def link_listing(prop, *, force: bool = False) -> bool:
    """Set ``prop.location_ref`` from its city/location text. Never raises; returns True if set."""
    if prop.location_ref_id and not force:
        return False
    try:
        loc = match_location(prop.market_id, prop.city, prop.location)
    except Exception:                                # matching must never fail a listing write
        logger.exception('location matching failed for listing %s', prop.pk)
        return False
    if loc is None and force and prop.location_ref_id:
        type(prop).objects.filter(pk=prop.pk).update(location_ref=None)   # stale link (e.g. market moved)
        prop.location_ref = None
        return False
    if loc is None or loc.pk == prop.location_ref_id:
        return False
    type(prop).objects.filter(pk=prop.pk).update(location_ref=loc)   # trigger: public point
    prop.location_ref = loc
    if all(f in prop.__dict__ for f in type(prop)._GEO_INPUTS):
        prop.geo_lat, prop.geo_lng = prop._public_point()            # keep the instance in step
    return True


def link_unmatched(batch_size: int = 500, limit: int | None = None) -> dict:
    """Match listings without a location_ref, in batches. Returns counts."""
    from apps.properties.models import Property
    stats = {'checked': 0, 'linked': 0}
    last_pk = None
    while limit is None or stats['checked'] < limit:
        qs = (Property.objects.filter(location_ref__isnull=True, market__isnull=False)
              .exclude(city='').only('id', 'city', 'location', 'market_id', 'location_ref_id')
              .order_by('pk'))
        if last_pk is not None:
            qs = qs.filter(pk__gt=last_pk)
        rows = list(qs[:batch_size])
        if not rows:
            break
        for p in rows:
            stats['checked'] += 1
            stats['linked'] += link_listing(p)
        last_pk = rows[-1].pk
    return stats


def report() -> dict:
    """Coverage for the M1 exit criterion: linked share, per-level and review-queue sizes."""
    from django.db.models import Count
    from apps.properties.models import Property
    from .models import Location
    total = Property.objects.count()
    unlinked = Property.objects.filter(location_ref__isnull=True).count()
    by_level = dict(Property.objects.exclude(location_ref=None)
                    .values_list('location_ref__level').annotate(n=Count('id')))
    on_unverified = Property.objects.filter(location_ref__is_verified=False).count()
    return {
        'listings': total, 'unlinked': unlinked,
        'unlinked_pct': round(100 * unlinked / total, 2) if total else 0.0,
        'linked_by_level': by_level,
        'on_unverified_location': on_unverified,
        'review_queue': Location.objects.filter(is_verified=False, is_active=True).count(),
    }
