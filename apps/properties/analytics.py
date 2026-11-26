"""
Listing analytics counters (T-3060, FR-LST-012, ADR-058).

Each event adds one to a per-listing, per-day row (``ListingDailyStat``). The day
is the local day of the listing's market. Nothing about the person is stored:
detail views are counted once per visitor per day with a short-lived key in the
cache (a keyed hash of the day, listing, address and browser), which expires
after the day and never reaches the database.

Counting must never break the request it rides on: errors are logged and dropped.
"""
from __future__ import annotations

import logging
import re

from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

logger = logging.getLogger(__name__)

IMPRESSIONS, DETAIL_VIEWS, SAVES, ENQUIRIES = 'impressions', 'detail_views', 'saves', 'enquiries'
COUNTERS = (IMPRESSIONS, DETAIL_VIEWS, SAVES, ENQUIRIES)
SEEN_TTL = 36 * 3600              # longer than any local day, so a key outlives the day it is for

_BOT = re.compile(r'bot\b|bot/|crawl|spider|slurp|facebookexternalhit|preview|headless|lighthouse|'
                  r'curl/|wget/|python-requests|python-urllib|httpclient|go-http-client|uptime|monitor', re.I)


def is_bot(request) -> bool:
    """Crawlers, link previews and scripts that say what they are."""
    return bool(_BOT.search(request.META.get('HTTP_USER_AGENT', '')))


def local_day(listing):
    from apps.markets.services import market_timezone
    return timezone.now().astimezone(market_timezone(listing.market_id)).date()


def record(counter: str, listings) -> None:
    """Add one to ``counter`` for each listing (once per listing per call)."""
    if counter not in COUNTERS:
        raise ValueError(counter)
    rows = {}
    for p in listings:
        if p is not None and p.pk not in rows:
            rows[p.pk] = local_day(p)
    if not rows:
        return
    # A fixed order, so two concurrent upserts lock rows in the same order.
    ordered = sorted(rows.items(), key=lambda kv: str(kv[0]))
    values = ', '.join(['(%s, %s, 1)'] * len(ordered))
    params = [x for pk_day in ordered for x in pk_day]
    # ``counter`` is one of COUNTERS (checked above); every value is a parameter.
    sql = (f'INSERT INTO listing_daily_stats (listing_id, day, {counter}) VALUES {values} '  # nosec B608
           f'ON CONFLICT (listing_id, day) DO UPDATE SET {counter} = listing_daily_stats.{counter} + 1')
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, params)
    except DatabaseError:
        logger.warning('listing_analytics.write_failed counter=%s listings=%d', counter, len(ordered), exc_info=True)


def record_on_commit(counter: str, listing) -> None:
    """For events inside a transaction: counted only if it commits."""
    transaction.on_commit(lambda: record(counter, [listing]))


def _visitor(request) -> str:
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return f'u:{user.pk}'
    from rest_framework.throttling import BaseThrottle
    ident = BaseThrottle().get_ident(request)       # honours NUM_PROXIES, as the rate limits do
    return f'a:{ident}:{request.META.get("HTTP_USER_AGENT", "")[:200]}'


def first_view_today(visitor: str, listing) -> bool:
    """True the first time this visitor opens this listing on this local day."""
    from django.core.cache import cache
    key = 'listing-view:' + salted_hmac('listing-analytics', f'{local_day(listing)}|{listing.pk}|{visitor}',
                                        algorithm='sha256').hexdigest()[:40]
    try:
        return cache.add(key, 1, timeout=SEEN_TTL)
    except Exception:  # noqa: BLE001 — a cache outage must not hide the listing; count the view
        logger.warning('listing_analytics.cache_unavailable', exc_info=True)
        return True


def _managed_by(user, listing) -> bool:
    """The lister's own side looking at the listing does not count."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    from apps.organizations.models import OrganizationMembership
    from apps.properties.lifecycle import can_manage
    if can_manage(user, listing):
        return True
    return bool(listing.organization_id and OrganizationMembership.objects.filter(
        user=user, organization_id=listing.organization_id, is_active=True).exists())


def detail_viewed(request, listing) -> None:
    if is_bot(request) or _managed_by(getattr(request, 'user', None), listing):
        return
    if first_view_today(_visitor(request), listing):
        record(DETAIL_VIEWS, [listing])


def detail_viewed_in_chat(user, listing) -> None:
    """The assistant showed one listing's details to a person (WhatsApp or web chat)."""
    if user is None or _managed_by(user, listing):
        return
    if first_view_today(f'u:{user.pk}', listing):
        record(DETAIL_VIEWS, [listing])


def shown(request, listings) -> None:
    """Listings shown in one page of search results. ``request`` is None for the assistant."""
    listings = list(listings)
    if not listings or (request is not None and is_bot(request)):
        return
    user = getattr(request, 'user', None) if request is not None else None
    if user is not None and getattr(user, 'is_authenticated', False):
        from apps.agents.models import Agent
        from apps.organizations.models import OrganizationMembership
        orgs = set(OrganizationMembership.objects.filter(user=user, is_active=True)
                   .values_list('organization_id', flat=True))
        agents = set(Agent.objects.filter(user=user).values_list('pk', flat=True))
        listings = [p for p in listings if not (p.organization_id in orgs or p.owner_id == user.pk
                                                or p.listed_by_agent_id in agents)]
    record(IMPRESSIONS, listings)
