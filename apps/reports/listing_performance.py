"""
Listing performance (T-3060, FR-LST-012, ADR-058).

Per listing, over a period of local days, from ``ListingDailyStat``: how often it
was shown in search results, opened, saved and enquired about, and the rates
between those steps. Aggregate counts only; nobody who looked is named.

Who sees which listings:

* managers of the active organization (owner, org admin, team or sales manager):
  every listing of the organization;
* anyone: listings they own (private sellers), list as a freelance agent, or are
  the assigned agent of within their active organization;
* platform admins: the organization or listing they name.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Exists, IntegerField, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.properties.analytics import COUNTERS

SORTS = COUNTERS
DEFAULT_LIMIT, MAX_LIMIT = 50, 200


def scope_q(user, org, manager: bool) -> Q:
    q = Q(owner=user) | Q(listed_by_agent__user=user)
    if org is not None:
        q |= Q(organization=org, assigned_agent__user=user)
        if manager:
            q |= Q(organization=org)
    return q


def _pct(part, whole):
    return round(part * 100 / whole, 1) if whole else None


def _rates(row: dict) -> dict:
    return {**row,
            'view_rate_pct': _pct(row['detail_views'], row['impressions']),       # opened / shown
            'save_rate_pct': _pct(row['saves'], row['detail_views']),             # saved / opened
            'enquiry_rate_pct': _pct(row['enquiries'], row['detail_views'])}      # enquired / opened


def report(listings, p, *, sort: str = 'impressions', limit: int = DEFAULT_LIMIT, offset: int = 0,
           daily_for=None) -> dict:
    """
    ``listings`` is the caller's scope (a ``Property`` queryset). Rows are listings
    that had any activity in the period or are live now, most ``sort`` first.
    ``daily_for`` (one listing of the scope) adds its day-by-day series.
    """
    from apps.properties.models import ListingDailyStat, Property
    in_period = Q(daily_stats__day__gte=p.first, daily_stats__day__lte=p.last)
    # Prefixed: ``enquiries`` is also a relation on Property.
    sums = {f'n_{c}': Coalesce(Sum(f'daily_stats__{c}', filter=in_period), Value(0), output_field=IntegerField())
            for c in COUNTERS}
    active = ListingDailyStat.objects.filter(listing=OuterRef('pk'), day__gte=p.first, day__lte=p.last)
    rows_qs = (listings.annotate(_active=Exists(active))
               .filter(Q(_active=True) | Q(is_active=True, status__in=Property.VISIBLE_STATUSES))
               .annotate(**sums))
    count = rows_qs.count()
    page = rows_qs.order_by(f'-n_{sort}', '-created_at', 'pk').values(
        'pk', 'ref_no', 'title', 'status', *sums)[offset:offset + limit]
    rows = [_rates({'listing_id': str(r['pk']), 'ref_no': r['ref_no'], 'title': r['title'], 'status': r['status'],
                    **{c: r[f'n_{c}'] for c in COUNTERS}}) for r in page]

    totals = ListingDailyStat.objects.filter(listing__in=listings.values('pk'), day__gte=p.first,
                                             day__lte=p.last).aggregate(**{c: Sum(c) for c in COUNTERS})
    out = {
        'from': p.first.isoformat(), 'to': p.last.isoformat(), 'timezone': p.tz.key,
        'count': count, 'limit': limit, 'offset': offset, 'sort': sort,
        'listings': rows,
        'totals': _rates({c: totals[c] or 0 for c in COUNTERS}),
    }
    if daily_for is not None:
        by_day = {r['day']: r for r in ListingDailyStat.objects.filter(
            listing=daily_for, day__gte=p.first, day__lte=p.last).values('day', *COUNTERS)}
        days = (p.first + timedelta(days=i) for i in range((p.last - p.first).days + 1))
        out['daily'] = [{'day': d.isoformat(), **{c: by_day.get(d, {}).get(c, 0) for c in COUNTERS}} for d in days]
    return out
