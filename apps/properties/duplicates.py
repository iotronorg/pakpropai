"""
Persistent duplicate clustering and fraud signals v2 (T-4050, FR-LST-010, ADR-069).

Two listings are linked when either
- **fingerprint**: same ``duplicate_key`` (country, place cell, purpose, category,
  bedrooms, currency, price type) and size and price each within ``TOLERANCE``; or
- **image**: they share a photo (same ``sha256``, or ``dhash`` within ``MAX_HAMMING`` bits)
  in the same country.
Linked listings share a ``DuplicateCluster``; clusters are kept on save (after commit) and
rebuilt nightly. A moderator can confirm a cluster, choose its canonical listing, or mark
a listing "not a duplicate" (it is never clustered automatically again).

Fraud signals (codes for the moderation queue, never automatic decisions):
- ``image_reused``: a photo also on a listing of another lister;
- ``reposted_after_takedown``: linked to a listing that was suspended or rejected.
"""
from __future__ import annotations

import hashlib
import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.images import hamming

logger = logging.getLogger(__name__)

TOLERANCE = Decimal('0.05')
MAX_HAMMING = 4
MAX_CANDIDATES = 300
CELL = Decimal('0.001')          # ~110 m on the public (snapped) coordinates


def _norm(text) -> str:
    return ' '.join(str(text or '').lower().replace(',', ' ').split())


def place_cell(p) -> str:
    if p.geo_lat is not None and p.geo_lng is not None:
        return f'g:{Decimal(p.geo_lat).quantize(CELL)}:{Decimal(p.geo_lng).quantize(CELL)}'
    if p.location_ref_id:
        return f'l:{p.location_ref_id}'
    return f't:{_norm(p.city)}|{_norm(p.location)}'


def key_for(p) -> str:
    """The exact-match part of the fingerprint; '' when the listing cannot be fingerprinted."""
    if not p.country or (p.size_m2 is None and p.size_value is None):
        return ''
    raw = '|'.join(str(x) for x in (p.country.upper(), place_cell(p), p.purpose, p.category, p.bedrooms,
                                     (p.currency or '').upper(), p.price_type))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _size(p):
    return p.size_m2 if p.size_m2 is not None else p._compute_size_m2()


def _close(a, b) -> bool:
    if a is None or b is None:
        return False
    a, b = Decimal(a), Decimal(b)
    return abs(a - b) <= max(a, b) * TOLERANCE


def _same_terms(a, b) -> bool:
    if not _close(_size(a), _size(b)):
        return False
    if a.price_on_request or b.price_on_request:
        return a.price_on_request and b.price_on_request
    return _close(a.price, b.price)


def _pool():
    """Listings that take part: anything not deleted or archived (taken-down ones too, for reposts)."""
    from .models import Property
    S = Property.Status
    return Property.objects.exclude(status__in=(S.ARCHIVED,)).filter(duplicate_distinct=False)


def fingerprint_matches(p) -> list:
    key = p.duplicate_key or key_for(p)
    if not key:
        return []
    rows = _pool().filter(duplicate_key=key).exclude(pk=p.pk)[:MAX_CANDIDATES]
    return [q for q in rows if _same_terms(p, q)]


def image_matches(p) -> list:
    """Listings sharing a photo with ``p`` (same country): {listing: [image pairs]}."""
    from .models import PropertyImage
    mine = list(PropertyImage.objects.filter(property=p).exclude(sha256='').values_list('sha256', 'dhash'))
    if not mine:
        return []
    shas = {s for s, _ in mine}
    hashes = [h for _, h in mine if h]
    others = (PropertyImage.objects.filter(property__country=p.country).exclude(property=p)
              .filter(property__in=_pool()).exclude(sha256='')
              .values_list('property_id', 'sha256', 'dhash')[:5000])
    hit = set()
    for pid, sha, h in others:
        if sha in shas or (h and any(hamming(h, x) <= MAX_HAMMING for x in hashes)):
            hit.add(pid)
    from .models import Property
    return list(Property.objects.filter(pk__in=hit))


def _lister(p) -> tuple:
    return ('org', p.organization_id) if p.organization_id else ('user', p.owner_id) if p.owner_id \
        else ('platform', None)


def _taken_down(p) -> bool:
    from apps.moderation.models import ModerationItem
    from .models import Property
    if p.status == Property.Status.SUSPENDED:
        return True
    return ModerationItem.objects.filter(listing_id=p.pk, status__in=('rejected', 'suspended')).exists()


def signals(p) -> list[str]:
    """Fraud signals v2 for the moderation checks."""
    out = []
    by_image = image_matches(p)
    if any(_lister(q) != _lister(p) for q in by_image):
        out.append('image_reused')
    linked = {q.pk: q for q in [*by_image, *fingerprint_matches(p)]}
    if any(_taken_down(q) for q in linked.values()):
        out.append('reposted_after_takedown')
    return out


# ── Clusters ──────────────────────────────────────────────────────────────────

def refresh(p) -> 'DuplicateCluster | None':
    """Re-key ``p`` and put it in the right cluster (or none). Idempotent."""
    from .models import DuplicateCluster, Property
    with transaction.atomic():
        p = Property.objects.select_for_update().get(pk=p.pk)
        key = key_for(p)
        if key != p.duplicate_key:
            Property.objects.filter(pk=p.pk).update(duplicate_key=key)
            p.duplicate_key = key
        if p.duplicate_distinct or p.status == Property.Status.ARCHIVED:
            _leave(p)
            return None
        by_fp, by_img = fingerprint_matches(p), image_matches(p)
        linked = {q.pk: q for q in [*by_fp, *by_img]}
        if not linked:
            _leave(p)
            return None
        clusters = {q.duplicate_cluster_id for q in linked.values() if q.duplicate_cluster_id}
        if p.duplicate_cluster_id:
            clusters.add(p.duplicate_cluster_id)
        if clusters:
            found = list(DuplicateCluster.objects.filter(pk__in=clusters).order_by('created_at'))
            target = next((c for c in found if c.status == DuplicateCluster.Status.CONFIRMED), found[0])
            others = [c.pk for c in found if c.pk != target.pk]
            if others:
                Property.objects.filter(duplicate_cluster_id__in=others).update(duplicate_cluster=target)
                DuplicateCluster.objects.filter(pk__in=others).delete()
        else:
            target = DuplicateCluster.objects.create(canonical=_oldest([p, *linked.values()]))
        Property.objects.filter(pk__in=[p.pk, *linked]).update(duplicate_cluster=target)
        target.evidence = {'fingerprint': len(by_fp), 'image': len(by_img)}
        if target.canonical_id is None:
            target.canonical = _oldest(list(target.listings.all()))
        target.save(update_fields=['evidence', 'canonical', 'updated_at'])
        return target


def _oldest(listings):
    return min(listings, key=lambda q: (q.published_at or q.created_at, str(q.pk)))


def _leave(p):
    from .models import DuplicateCluster, Property
    cid = p.duplicate_cluster_id
    if cid is None:
        return
    Property.objects.filter(pk=p.pk).update(duplicate_cluster=None)
    rest = list(Property.objects.filter(duplicate_cluster_id=cid))
    if len(rest) < 2:
        Property.objects.filter(duplicate_cluster_id=cid).update(duplicate_cluster=None)
        DuplicateCluster.objects.filter(pk=cid).delete()
    else:
        DuplicateCluster.objects.filter(pk=cid, canonical_id=p.pk).update(canonical=_oldest(rest))


def rebuild(batch: int = 500) -> int:
    """Nightly: re-check every listing that takes part (also clears stale clusters)."""
    from .models import Property
    n = 0
    for pk in Property.objects.exclude(status=Property.Status.ARCHIVED).values_list('pk', flat=True).iterator(
            chunk_size=batch):
        try:
            refresh(Property(pk=pk))
            n += 1
        except Property.DoesNotExist:
            continue
    for pk in Property.objects.filter(status=Property.Status.ARCHIVED, duplicate_cluster__isnull=False) \
            .values_list('pk', flat=True):
        refresh(Property(pk=pk))
    return n


# ── Moderator actions ─────────────────────────────────────────────────────────

class ClusterError(Exception):
    pass


def confirm(cluster, user, canonical_id=None):
    from .models import DuplicateCluster
    with transaction.atomic():
        c = DuplicateCluster.objects.select_for_update().get(pk=cluster.pk)
        if canonical_id is not None:
            if not c.listings.filter(pk=canonical_id).exists():
                raise ClusterError('The canonical listing must be in this cluster.')
            c.canonical_id = canonical_id
        c.status, c.reviewed_by, c.reviewed_at = DuplicateCluster.Status.CONFIRMED, user, timezone.now()
        c.save(update_fields=['status', 'canonical', 'reviewed_by', 'reviewed_at', 'updated_at'])
    _audit('listing.duplicates_confirmed', c, user, {'canonical': str(c.canonical_id)})
    return c


def split(cluster, user, listing_id):
    """Mark one listing "not a duplicate": out of this cluster and of automatic clustering."""
    from .models import Property
    p = cluster.listings.filter(pk=listing_id).first()
    if p is None:
        raise ClusterError('That listing is not in this cluster.')
    Property.objects.filter(pk=p.pk).update(duplicate_distinct=True)
    p.duplicate_distinct = True
    _leave(p)
    _audit('listing.duplicate_split', cluster, user, {'listing': str(p.pk)})


def _audit(action, cluster, user, meta):
    from apps.core.audit import safe_record
    safe_record(action, category='listing', actor=user, target=cluster, meta=meta)


def cluster_dict(c) -> dict:
    items = []
    for p in c.listings.select_related('organization').order_by('created_at'):
        items.append({'id': str(p.pk), 'title': p.title, 'status': p.status, 'price': p.price,
                      'currency': p.currency, 'size_m2': float(_size(p)) if _size(p) is not None else None,
                      'city': p.city, 'location': p.location, 'lister': list(_lister(p)),
                      'organization': p.organization.name if p.organization_id else None,
                      'canonical': p.pk == c.canonical_id, 'created_at': p.created_at})
    return {'id': str(c.pk), 'status': c.status, 'canonical': str(c.canonical_id) if c.canonical_id else None,
            'evidence': c.evidence, 'listings': items, 'updated_at': c.updated_at,
            'distinct_listers': len({tuple(i['lister']) for i in items})}

