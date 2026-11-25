"""Saved searches and their alerts (T-2040, MVP acceptance 5)."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.properties import criteria as C
from apps.properties import listing_search as S
from apps.properties.models import PriceHistory
from apps.properties.public_listings import public_text

from .models import AlertDelivery, Favourite, SavedSearch
from .services import PreferenceError, _lock

logger = logging.getLogger(__name__)

MAX_SEARCHES = 20
BATCH, SHOWN = 50, 3
F = SavedSearch.Frequency
INTERVAL = {F.INSTANT: timedelta(hours=1), F.DAILY: timedelta(days=1), F.WEEKLY: timedelta(days=7)}
INSTANT_CHECK = timedelta(minutes=15)
_SALT = 'saved-search-stop'


def clean(criteria: dict) -> C.SearchCriteria:
    """Raises CriteriaInvalid / LocationAmbiguous. A search needs at least a place."""
    if not isinstance(criteria, dict):
        raise C.CriteriaInvalid({'criteria': ['Must be an object.']})
    asked = C.parse({k: v for k, v in criteria.items() if k != 'missing_required'})
    crit, _ = C.prepare(asked)
    for name in ('amenities_required', 'accessibility'):     # dropping one would widen the alert silently
        if lost := [k for k in getattr(asked, name) if k not in getattr(crit, name)]:
            raise C.CriteriaInvalid({name: [f'Unknown: {", ".join(lost)}']})
    if 'location' in crit.missing_required:
        raise C.CriteriaInvalid({'location': ['Choose a place.']})
    crit.listed_within_days = None                 # "new" is what the alert itself means
    return crit


def dump(crit: C.SearchCriteria) -> dict:
    return {'market': crit.market, **crit.model_dump(mode='json', exclude_defaults=True,
                                                     exclude={'missing_required', 'confidence', 'unsupported_requests'})}


def describe(crit: C.SearchCriteria) -> str:
    """A short name built from the structured fields only."""
    from apps.locations.models import Location
    place = ', '.join(Location.objects.filter(pk__in=crit.location.location_ids[:2]).values_list('name', flat=True))
    place = place or crit.location.free_text or 'chosen area'
    what = (crit.subtypes[0] if crit.subtypes else crit.category or 'property').replace('_', ' ')
    rooms = f'{crit.bedrooms.min}+ bed ' if crit.bedrooms.min else ''
    how = {'sale': 'to buy', 'rent_long': 'to rent', 'rent_short': 'short rent', 'lease_commercial': 'to lease'}.get(
        crit.purpose, '')
    price = f' up to {crit.price.currency} {crit.price.max:,}' if crit.price.max else ''
    return ' '.join(f'{rooms}{what} {how} in {place}{price}'.split())[:100]


def create(user, criteria: dict, *, name: str = '', frequency: str = F.DAILY, channel: str = 'in_app',
           via: str = 'api') -> SavedSearch:
    """The caller has the person's consent to be alerted."""
    crit = clean(criteria)
    now = timezone.now()
    with transaction.atomic():
        _lock(user)
        if SavedSearch.objects.filter(user=user).count() >= MAX_SEARCHES:
            raise PreferenceError(f'You can keep {MAX_SEARCHES} saved searches.', 'limit')
        return SavedSearch.objects.create(
            user=user, name=(name or describe(crit))[:100], criteria=dump(crit), frequency=frequency, channel=channel,
            consented_at=now, consent_via=via, since=now, next_run_at=_next(frequency, now))


def _next(frequency, now):
    return now + (INSTANT_CHECK if frequency == F.INSTANT else INTERVAL[frequency])


def pause(search: SavedSearch, reason: str) -> None:
    SavedSearch.objects.filter(pk=search.pk).update(is_active=False, paused_reason=reason)


def resume(search: SavedSearch) -> None:
    """What appeared while paused is not announced."""
    now = timezone.now()
    SavedSearch.objects.filter(pk=search.pk).update(
        is_active=True, paused_reason='', since=now, next_run_at=_next(search.frequency, now))


# ── Stop link ─────────────────────────────────────────────────────────────────

def stop_token(search: SavedSearch) -> str:
    return signing.dumps(str(search.pk), salt=_SALT)


def stop(token: str) -> bool:
    """One click from an alert, without signing in. It can only pause this search."""
    try:
        pk = signing.loads(token, salt=_SALT)
    except signing.BadSignature:
        return False
    return bool(SavedSearch.objects.filter(pk=pk).update(is_active=False, paused_reason='unsubscribed'))


# ── Delivery ──────────────────────────────────────────────────────────────────

def new_matches(search: SavedSearch, crit: C.SearchCriteria):
    """
    Public matches listed, or repriced, since the search started; never announced
    before, not dismissed or already saved, not the person's own.
    """
    from django.db.models.functions import Coalesce
    repriced = PriceHistory.objects.filter(property=OuterRef('pk'), changed_at__gt=search.since)
    mine = Favourite.objects.filter(user=search.user, listing=OuterRef('pk'))
    told = AlertDelivery.objects.filter(saved_search=search, listing=OuterRef('pk'))
    return (S.filtered(crit).annotate(_since=Coalesce('published_at', 'created_at'))
            .filter(Q(_since__gt=search.since) | Exists(repriced))
            .exclude(Exists(mine)).exclude(Exists(told)).exclude(owner=search.user)
            .exclude(listed_by_agent__user=search.user).order_by('-_since', 'pk'))


def run(search_id) -> int:
    """Check one search. Listings are marked and the alert queued in one transaction."""
    from apps.notifications.services import notify_user
    now = timezone.now()
    with transaction.atomic():
        s = (SavedSearch.objects.select_for_update(skip_locked=True, of=('self',)).select_related('user')
             .filter(pk=search_id, is_active=True, next_run_at__lte=now, user__is_active=True).first())
        if s is None:
            return 0
        s.next_run_at = _next(s.frequency, now)
        try:
            crit = clean(s.criteria)
        except (C.CriteriaInvalid, C.LocationAmbiguous):     # place or market withdrawn
            s.is_active, s.paused_reason = False, 'criteria'
            s.save(update_fields=['is_active', 'paused_reason', 'next_run_at', 'updated_at'])
            notify_user(s.user, title='Saved search paused', message=f'“{s.name}” no longer works. Please edit it.',
                        send_whatsapp=False, event_type='saved_searches', path=f'/saved-searches/{s.pk}')
            return 0
        if s.frequency == F.INSTANT and s.last_alert_at and s.last_alert_at > now - INTERVAL[F.INSTANT]:
            s.save(update_fields=['next_run_at', 'updated_at'])
            return 0
        rows = list(new_matches(s, crit)[:BATCH])
        if rows:
            AlertDelivery.objects.bulk_create([AlertDelivery(saved_search=s, listing=p) for p in rows])
            s.last_alert_at = now
            count = f'{len(rows)}{"+" if len(rows) == BATCH else ""}'
            lines = [f'{count} new for “{s.name}”:'] + [
                f'• {public_text(p, p.title)[:80]} — {S.price_text(p)}' for p in rows[:SHOWN]]
            C_ = SavedSearch.Channel
            path = f'/saved-searches/{s.pk}?stop={stop_token(s)}'
            if s.channel == C_.WHATSAPP and settings.FRONTEND_URL:     # an email links to the page itself
                lines.append(f'See all or stop: {settings.FRONTEND_URL.rstrip("/")}{path}')
            ok = notify_user(s.user, title='New matches for your search', message='\n'.join(lines),
                             send_whatsapp=s.channel == C_.WHATSAPP, send_email=s.channel == C_.EMAIL,
                             event_type='saved_searches', path=path)
            if not ok:                # roll the marks back; run_due tries again in an hour
                raise RuntimeError('notification failed')
        s.save(update_fields=['next_run_at', 'last_alert_at', 'updated_at'])
    return len(rows)


def run_due(limit: int = 500) -> int:
    """Run every due search; one failure does not stop the others."""
    sent = 0
    due = list(SavedSearch.objects.filter(is_active=True, next_run_at__lte=timezone.now(), user__is_active=True)
               .order_by('next_run_at').values_list('pk', flat=True)[:limit])
    if len(due) == limit:            # alerts are falling behind their frequency (MVP acceptance 5)
        logger.warning('saved_search.backlog limit=%s', limit)
    for pk in due:
        try:
            sent += bool(run(pk))
        except Exception:
            logger.exception('saved_search.failed id=%s', pk)
            SavedSearch.objects.filter(pk=pk).update(next_run_at=timezone.now() + timedelta(hours=1))
    return sent
