"""
Listing lifecycle (FR-LST-005/006, T-1080, ADR-025).

    draft ─→ pending_review ─→ published ⇄ under_offer ─→ sold / let ─→ archived
      ↑            │               │  ↑                                   │
      └────────────┘               ↓  └── expired (system, on expires_at) ←┘
                                suspended (staff only)

``transition(prop, to, actor=…)`` is the only way the API, admin and jobs
change ``status``. It checks the edge, who may take it, the publish
preconditions, sets the timestamps, and the model writes the
``ListingTransition`` audit row in the same transaction.

Market settings (``Market.settings``, admin-editable; both off by default):

* ``listing_review_required`` (bool): owners submit to ``pending_review``;
  staff approve to ``published``.
* ``listing_ttl_days`` (int): a publish sets ``expires_at``; the daily job
  moves overdue listings to ``expired`` and reminds owners
  ``LISTING_EXPIRY_REMINDER_DAYS`` before. Renew = expired → published.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ListingTransition, Property

logger = logging.getLogger(__name__)
S = Property.Status

ALLOWED: dict[str, frozenset] = {
    S.DRAFT:          frozenset({S.PENDING_REVIEW, S.PUBLISHED, S.ARCHIVED}),
    S.PENDING_REVIEW: frozenset({S.PUBLISHED, S.DRAFT, S.ARCHIVED, S.SUSPENDED}),
    S.PUBLISHED:      frozenset({S.UNDER_OFFER, S.SOLD, S.LET, S.EXPIRED, S.ARCHIVED, S.DRAFT,
                                 S.SUSPENDED}),
    S.UNDER_OFFER:    frozenset({S.PUBLISHED, S.SOLD, S.LET, S.ARCHIVED, S.SUSPENDED}),
    S.SOLD:           frozenset({S.ARCHIVED}),
    S.LET:            frozenset({S.PENDING_REVIEW, S.PUBLISHED, S.ARCHIVED}),
    S.EXPIRED:        frozenset({S.PENDING_REVIEW, S.PUBLISHED, S.DRAFT, S.ARCHIVED}),
    S.ARCHIVED:       frozenset({S.DRAFT, S.PENDING_REVIEW, S.PUBLISHED}),
    S.SUSPENDED:      frozenset({S.DRAFT, S.PUBLISHED, S.ARCHIVED}),
}
# Entering 'published' from these starts a new publication (review, TTL).
_FRESH_PUBLISH_FROM = frozenset({S.DRAFT, S.PENDING_REVIEW, S.LET, S.EXPIRED, S.ARCHIVED, S.SUSPENDED})
_SALE_PURPOSES = frozenset({Property.Purpose.SALE})
_LET_PURPOSES = frozenset({Property.Purpose.RENT_LONG, Property.Purpose.RENT_SHORT,
                           Property.Purpose.LEASE_COMMERCIAL})


class TransitionError(Exception):
    """Base. ``code`` maps to the HTTP status in the view."""
    http_status = 409

    def __init__(self, message, *, fields: dict | None = None):
        super().__init__(message)
        self.fields = fields or {}


class InvalidTransition(TransitionError):
    http_status = 409


class TransitionForbidden(TransitionError):
    http_status = 403


class PublishBlocked(TransitionError):
    """Required fields missing (field-level 400)."""
    http_status = 400


def is_staff(user) -> bool:
    return bool(user and getattr(user, 'is_authenticated', False)
                and (getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser))


def can_manage(user, prop) -> bool:
    """Owner, the listing agent, or staff (the edit rule, plus the listing agent)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if is_staff(user) or (prop.owner_id and prop.owner_id == user.id):
        return True
    agent = getattr(prop, 'listed_by_agent', None) if prop.listed_by_agent_id else None
    return bool(agent and getattr(agent, 'user_id', None) == user.id)


def market_settings(prop) -> dict:
    from apps.markets.services import get_market
    info = get_market(prop.market_id) if prop.market_id else None
    return dict(info.settings) if info else {}


def review_required(prop) -> bool:
    return bool(market_settings(prop).get('listing_review_required', False))


def ttl_days(prop) -> int | None:
    v = market_settings(prop).get('listing_ttl_days')
    try:
        v = int(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None
    return v if v and v > 0 else None


def publish_problems(prop) -> dict:
    """FR-LST business rules for going public (field → message)."""
    out = {}
    if not (prop.title or '').strip():
        out['title'] = 'Required to publish.'
    if not prop.purpose:
        out['purpose'] = 'Required to publish.'
    if not prop.category:
        out['category'] = 'Required to publish.'
    if prop.price is None and not prop.price_on_request:
        out['price'] = 'Set a price or mark it price on request.'
    if not (prop.city or '').strip() and not prop.location_ref_id:
        out['city'] = 'Required to publish.'
    if not (prop.location or '').strip() and not prop.location_ref_id:
        out['location'] = 'Required to publish.'
    return out


def allowed_targets(prop, actor, *, system: bool = False) -> list[str]:
    """Targets ``actor`` may move ``prop`` to now (for clients' buttons)."""
    out = []
    for to in sorted(ALLOWED.get(prop.status, ())):
        try:
            _check(prop, to, actor, system=system)
        except TransitionError:
            continue
        out.append(to)
    return out


def _check(prop, to, actor, *, system: bool):
    frm = prop.status
    if to not in S.values:
        raise InvalidTransition(f"Unknown status '{to}'.")
    if to == frm:
        raise InvalidTransition(f'Listing is already {frm}.')
    if to not in ALLOWED.get(frm, ()):
        raise InvalidTransition(f"Cannot move a listing from '{frm}' to '{to}'.")
    staff = system or is_staff(actor)
    if not system and not can_manage(actor, prop):
        raise TransitionForbidden('You cannot change this listing.')
    if to == S.EXPIRED and not system:
        raise InvalidTransition('Listings expire automatically.')
    if (to == S.SUSPENDED or frm == S.SUSPENDED and to != S.ARCHIVED) and not staff:
        raise TransitionForbidden('Only moderators can suspend or reinstate a listing.')
    if to == S.PUBLISHED and frm in _FRESH_PUBLISH_FROM and not staff and review_required(prop):
        if frm == S.PENDING_REVIEW:
            raise TransitionForbidden('Waiting for review by a moderator.')
        raise InvalidTransition('This market reviews listings first: move it to pending_review.')
    if to == S.SOLD and prop.purpose not in _SALE_PURPOSES:
        raise InvalidTransition("Only a listing for sale can be marked 'sold'.")
    if to == S.LET and prop.purpose not in _LET_PURPOSES:
        raise InvalidTransition("Only a rental or lease listing can be marked 'let'.")


def transition(prop: Property, to: str, *, actor=None, reason: str = '',
               source: str = ListingTransition.Source.API, system: bool = False) -> Property:
    """Move ``prop`` to ``to`` or raise a TransitionError. Locks the row."""
    with transaction.atomic():
        locked = Property.objects.select_for_update().get(pk=prop.pk)
        # Keep unsaved field edits of ``prop`` (perform_update saves first).
        prop.status, prop.is_active = locked.status, locked.is_active
        prop._status_loaded = (locked.status, locked.is_active)
        frm = prop.status
        _check(prop, to, actor, system=system)
        if to in (S.PUBLISHED, S.PENDING_REVIEW) and (problems := publish_problems(prop)):
            raise PublishBlocked('The listing is not complete enough to publish.', fields=problems)
        now = timezone.now()
        prop.status = to
        fields = ['status', 'is_active']
        if to == S.PUBLISHED and frm in _FRESH_PUBLISH_FROM:
            days = ttl_days(prop)
            prop.published_at = now
            prop.expires_at = now + timedelta(days=days) if days else None
            prop.expiry_reminded_at = None
            fields += ['published_at', 'expires_at', 'expiry_reminded_at']
        prop._transition_ctx = {'actor': actor if getattr(actor, 'pk', None) else None,
                                'reason': reason, 'source': source}
        prop.save(update_fields=fields)
    logger.info('listing.transition id=%s %s→%s source=%s', prop.pk, frm, to, source)
    return prop


def initial_status(requested: str | None, prop_like, actor) -> str:
    """Status for a new listing. Default published (as before T-1080); review markets → pending_review."""
    to = requested or S.PUBLISHED
    if to not in (S.DRAFT, S.PENDING_REVIEW, S.PUBLISHED):
        raise InvalidTransition(f"A new listing can start as draft, pending_review or published, not '{to}'.")
    if to == S.PUBLISHED and not is_staff(actor) and review_required(prop_like):
        return S.PENDING_REVIEW
    return to


def on_create(prop: Property, actor) -> None:
    """After insert: stamp the first publication and write the 'created' audit row."""
    if prop.status == S.PUBLISHED:
        days = ttl_days(prop)
        prop.published_at = timezone.now()
        prop.expires_at = prop.published_at + timedelta(days=days) if days else None
        Property.objects.filter(pk=prop.pk).update(published_at=prop.published_at,
                                                   expires_at=prop.expires_at)
    if getattr(actor, 'pk', None):
        ListingTransition.objects.filter(property=prop, from_status='').update(actor=actor)


# ── Expiry (daily beat) ───────────────────────────────────────────────────────

def expire_due(now=None, limit: int = 2000) -> int:
    """published listings past expires_at → expired (one audited transition each)."""
    now = now or timezone.now()
    ids = list(Property.objects.filter(status=S.PUBLISHED, expires_at__lte=now)
               .order_by('expires_at').values_list('pk', flat=True)[:limit])
    done = 0
    for pk in ids:
        prop = Property.objects.filter(pk=pk).first()
        if prop is None:
            continue
        try:
            transition(prop, S.EXPIRED, reason='Listing period ended.',
                       source=ListingTransition.Source.SYSTEM, system=True)
            done += 1
        except TransitionError:          # changed meanwhile (e.g. under offer)
            continue
    return done


def remind_expiring(now=None, limit: int = 2000) -> int:
    """Notify owners once, LISTING_EXPIRY_REMINDER_DAYS before expiry."""
    from apps.notifications.services import notify_user
    now = now or timezone.now()
    horizon = now + timedelta(days=settings.LISTING_EXPIRY_REMINDER_DAYS)
    qs = (Property.objects.filter(status=S.PUBLISHED, expires_at__gt=now, expires_at__lte=horizon,
                                  expiry_reminded_at__isnull=True, owner__isnull=False)
          .select_related('owner').order_by('expires_at')[:limit])
    sent = 0
    for prop in qs:
        # Claim first so a re-run (or a second worker) never reminds twice.
        if not Property.objects.filter(pk=prop.pk, expiry_reminded_at__isnull=True).update(
                expiry_reminded_at=now):
            continue
        notify_user(prop.owner, 'Listing expiring soon',
                    f'Your listing {prop.ref_no} "{prop.title[:60]}" expires on '
                    f'{prop.expires_at:%Y-%m-%d}. Renew it to keep it visible.',
                    event_type='lead_updates')
        sent += 1
    return sent
