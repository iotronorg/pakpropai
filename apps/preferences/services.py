"""Favourites and the consented preference profile (T-2040, ADR-038)."""
from __future__ import annotations

from collections import Counter

from django.db import transaction
from django.utils import timezone

from apps.compliance.models import ConsentRecord
from apps.properties import criteria as C
from apps.properties.models import Property

from .models import Favourite, PreferenceItem

MAX_FAVOURITES = 500
MEMORY_TEXT = ('RealTron will remember these preferences and use them in later searches. '
               'You can see, change or delete them at any time.')
# SearchCriteria fields that may be remembered. Free text never is: it can hold anything.
KEYS = ('purpose', 'category', 'subtypes', 'location', 'price', 'bedrooms', 'bathrooms', 'size',
        'amenities_preferred', 'furnishing', 'completion', 'pets_allowed')
INFER_FROM, INFER_MIN, INFER_SHARE = 20, 3, 0.6
_MEMORY = ConsentRecord.Purpose.AI_MEMORY


class PreferenceError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


# ── Favourites ────────────────────────────────────────────────────────────────

def visible(ids) -> dict:
    return {p.pk: p for p in Property.objects.visible_to(None).filter(pk__in=ids)}


def keep(user, listing: Property, *, kind: str = Favourite.Kind.SAVED, note: str | None = None) -> Favourite:
    with transaction.atomic():
        _lock(user)
        if (not Favourite.objects.filter(user=user, listing=listing).exists()
                and Favourite.objects.filter(user=user).count() >= MAX_FAVOURITES):
            raise PreferenceError(f'You can keep {MAX_FAVOURITES} listings.', 'limit')
        fields = {'kind': kind, **({'note': note} if note is not None else {})}
        fav, _ = Favourite.objects.update_or_create(user=user, listing=listing, defaults=fields)
        infer(user)
    return fav


def drop(user, listing_id) -> bool:
    with transaction.atomic():
        _lock(user)
        n, _ = Favourite.objects.filter(user=user, listing_id=listing_id).delete()
        infer(user)
    return bool(n)


def _lock(user) -> None:
    from django.contrib.auth import get_user_model
    get_user_model().objects.select_for_update().get(pk=user.pk)


# ── Consent ───────────────────────────────────────────────────────────────────

def remembers(user) -> bool:
    return user is not None and ConsentRecord.objects.filter(user=user, purpose=_MEMORY, is_active=True).exists()


def set_memory(user, on: bool) -> None:
    """Withdrawing deletes the whole profile at once."""
    with transaction.atomic():
        _lock(user)
        ConsentRecord.objects.update_or_create(user=user, purpose=_MEMORY, defaults={
            'is_active': on, 'revoked_at': None if on else timezone.now()})
        if on:
            infer(user)
        else:
            PreferenceItem.objects.filter(user=user).delete()


# ── Profile ───────────────────────────────────────────────────────────────────

def clean(market: str, values: dict) -> dict:
    """Validated criteria fragments. Raises CriteriaInvalid or LocationAmbiguous."""
    if bad := [k for k in values if k not in KEYS]:
        raise C.CriteriaInvalid({k: ['This cannot be remembered.'] for k in bad})
    if any(not isinstance(v, (dict, list, str, bool)) for v in values.values()):
        raise C.CriteriaInvalid({'values': ['Invalid value.']})
    crit, _ = C.prepare(C.parse({**values, 'market': market}))
    dumped = crit.model_dump(mode='json', exclude_defaults=True)
    out = {k: dumped[k] for k in values if dumped.get(k) not in (None, {}, [])}
    if 'location' in out:
        out['location'] = {k: v for k, v in out['location'].items() if k == 'location_ids'}
        if not out['location']:
            out.pop('location')
    return out


def state(user, market: str, values: dict) -> list[PreferenceItem]:
    """Store stated preferences. The person must have given the memory consent."""
    with transaction.atomic():
        _lock(user)
        if not remembers(user):
            raise PreferenceError('Turn on preference memory first.', 'consent_required')
        market = market.upper()
        out = []
        for key, value in clean(market, values).items():
            item, _ = PreferenceItem.objects.update_or_create(
                user=user, market_id=market, key=key, source=PreferenceItem.Source.STATED,
                defaults={'value': value, 'confidence': 1.0})
            out.append(item)
    return out


def stated(user, market: str) -> dict:
    """The person's own stated preferences for this market, or {} without consent."""
    if not remembers(user):
        return {}
    return {i.key: i.value for i in PreferenceItem.objects.filter(
        user=user, market_id=market, source=PreferenceItem.Source.STATED)}


def infer(user) -> None:
    """Rebuild the inferred items from the latest saved listings (only with consent)."""
    if not remembers(user):
        return
    PreferenceItem.objects.filter(user=user, source=PreferenceItem.Source.INFERRED).delete()
    by_market: dict = {}
    for f in (Favourite.objects.filter(user=user, kind=Favourite.Kind.SAVED, listing__market__isnull=False)
              .select_related('listing').order_by('-updated_at')[:INFER_FROM]):
        by_market.setdefault(f.listing.market_id, []).append(f.listing)
    rows = []
    for market, listings in by_market.items():
        if len(listings) < INFER_MIN:
            continue
        n = len(listings)

        def add(key, value, count):
            rows.append(PreferenceItem(user=user, market_id=market, key=key, value=value,
                                       source=PreferenceItem.Source.INFERRED, confidence=round(count / n, 2)))
        for key in ('purpose', 'category'):
            value, count = Counter(getattr(p, key) for p in listings).most_common(1)[0]
            if value and count / n >= INFER_SHARE:
                add(key, value, count)
        rooms = sorted(p.bedrooms for p in listings if p.bedrooms is not None)
        if len(rooms) / n >= INFER_SHARE:
            add('bedrooms', {'min': rooms[0], 'max': rooms[-1]}, len(rooms))
        prices = [(p.currency, p.price_type, p.price) for p in listings if p.price and not p.price_on_request]
        if prices:
            (currency, kind), count = Counter((c, t) for c, t, _ in prices).most_common(1)[0]
            period = {'total': 'total', 'per_month': 'month', 'per_year': 'year'}.get(kind)
            if period and count / n >= INFER_SHARE:
                top = max(v for c, t, v in prices if (c, t) == (currency, kind))
                add('price', {'max': top, 'currency': currency, 'period': period}, count)
    PreferenceItem.objects.bulk_create(rows)


def purge() -> int:
    """Items not confirmed for a long time are stale (AI spec §6)."""
    from datetime import timedelta

    from django.conf import settings
    n, _ = PreferenceItem.objects.filter(
        updated_at__lt=timezone.now() - timedelta(days=settings.PREFERENCE_RETENTION_DAYS)).delete()
    return n


def erase_user(user) -> int:
    from .models import SavedSearch
    n = 0
    for model in (SavedSearch, Favourite, PreferenceItem):
        n += model.objects.filter(user=user).delete()[0]
    ConsentRecord.objects.filter(user=user, purpose=_MEMORY).update(is_active=False, revoked_at=timezone.now())
    return n
