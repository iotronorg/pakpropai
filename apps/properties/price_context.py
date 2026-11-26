"""
Indicative price context (T-4020, FR-VAL-001…004, ADR-066).

The distribution of **asking** price per m² of comparable published sale
listings on this platform: same country, currency, category (and subtype when
known), a size band, in the listing's locality when there are enough, otherwise
its city. Shown with its sample size, spread, data age, a confidence level and
provenance on every figure; nothing is shown below ``MIN_SAMPLE`` comparables.

It is not a valuation: no estimate of what the property is worth or would sell
for, no "over/under-priced" verdict; only where an asking price sits among other
asking prices.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

MIN_SAMPLE = getattr(settings, 'PRICE_CONTEXT_MIN_SAMPLE', 8)
MAX_SAMPLE = 1000
SIZE_BAND = Decimal('0.30')          # ±30% of the size
PROVENANCE = 'listing_asking_price'


def disclaimer(market_name: str) -> str:
    return (f'Not a valuation or appraisal. These are asking prices of listings on this platform in '
            f'{market_name}, not sale prices, and say nothing about this property\'s condition, title or '
            'value. Consult a licensed valuer before any transaction.')


def _q(d) -> int:
    return int(Decimal(d).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _quantile(sorted_vals: list[Decimal], q: float) -> Decimal:
    """Linear interpolation between closest ranks."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = Decimal(str(q)) * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _locality(loc):
    """The listing's locality (society/area), walking up from a sub-locality."""
    from apps.locations.models import Location
    seen = 0
    while loc is not None and seen < 6:
        if loc.level == Location.Level.LOCALITY:
            return loc
        if loc.level in (Location.Level.CITY, Location.Level.REGION, Location.Level.COUNTRY):
            return None
        loc, seen = loc.parent, seen + 1
    return None


def _base(*, country, currency, category, subtype, size_m2, exclude):
    from .models import Property
    P = Property
    qs = (Property.objects.visible_to(None)
          .filter(purpose=P.Purpose.SALE, country=country, currency=currency, price_on_request=False,
                  price__gt=0, price_type=P.PriceType.TOTAL, size_m2__gt=0))
    if category:
        qs = qs.filter(category=category)
    if subtype:
        qs = qs.filter(subtype=subtype)
    if size_m2:
        size = Decimal(size_m2)
        qs = qs.filter(size_m2__gte=size * (1 - SIZE_BAND), size_m2__lte=size * (1 + SIZE_BAND))
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    return qs


def _confidence(n: int, spread: Decimal, age_days: int) -> dict:
    reasons = []
    if n < 20:
        reasons.append('few_comparables')
    if spread > Decimal('0.6'):
        reasons.append('wide_spread')
    elif spread > Decimal('0.35'):
        reasons.append('moderate_spread')
    if age_days > 180:
        reasons.append('older_listings')
    level = 'high' if not reasons else 'medium' if (n >= 12 and 'wide_spread' not in reasons and len(reasons) == 1) \
        else 'low'
    return {'level': level, 'reasons': reasons}


def context(*, country: str, city: str, currency: str, size_m2, category=None, subtype=None, locality=None,
            asking_price=None, exclude=None, market_name: str = '') -> dict:
    """Price-per-m² context; ``available`` is False (with the reason) when it cannot be shown."""
    country, currency = (country or '').upper(), (currency or '').upper()
    if not size_m2 or Decimal(size_m2) <= 0:
        return {'available': False, 'reason': 'no_size'}
    base = _base(country=country, currency=currency, category=category, subtype=subtype, size_m2=size_m2,
                 exclude=exclude)
    scopes = []
    if locality is not None:
        scopes.append(('locality', locality.name,
                       base.filter(Q(location_ref=locality) | Q(location_ref__path__startswith=locality.path + '/'))))
    scopes.append(('city', city, base.filter(city__iexact=city)))
    rows, scope, place = [], None, None
    for scope, place, qs in scopes:
        rows = list(qs.values_list('price', 'size_m2', 'published_at', 'created_at')[:MAX_SAMPLE])
        if len(rows) >= MIN_SAMPLE:
            break
    if len(rows) < MIN_SAMPLE:
        return {'available': False, 'reason': 'too_few_comparables', 'n': len(rows), 'min_sample': MIN_SAMPLE}

    now = timezone.now()
    per = sorted(Decimal(p) / Decimal(s) for p, s, _, _ in rows)
    ages = sorted((now - (pub or made)).days for _, _, pub, made in rows)
    p25, p50, p75 = (_quantile(per, q) for q in (0.25, 0.5, 0.75))
    spread = (p75 - p25) / p50 if p50 else Decimal(0)
    age = int(median(ages))

    def fig(v):
        return {'value': _q(v), 'provenance': PROVENANCE}

    out = {
        'available': True, 'currency': currency, 'unit': 'per_m2',
        'comparables': {'n': len(rows), 'scope': scope, 'place': place, 'category': category, 'subtype': subtype,
                        'size_m2_band': [_q(Decimal(size_m2) * (1 - SIZE_BAND)), _q(Decimal(size_m2) * (1 + SIZE_BAND))],
                        'purpose': 'sale', 'as_of': now.date().isoformat()},
        'price_per_m2': {'p25': fig(p25), 'median': fig(p50), 'p75': fig(p75),
                         'min': fig(per[0]), 'max': fig(per[-1])},
        'dispersion': {'iqr_over_median': float(spread.quantize(Decimal('0.01'))),
                       'formula': '(p75 − p25) ÷ median'},
        'data_age': {'median_days_listed': age, 'oldest_days': ages[-1]},
        'confidence': _confidence(len(rows), spread, age),
        'disclaimer': disclaimer(market_name or country),
    }
    if asking_price:
        own = Decimal(asking_price) / Decimal(size_m2)
        below = sum(1 for v in per if v < own)
        same = sum(1 for v in per if v == own)
        rank = (below + same / 2) / len(per) * 100
        out['subject'] = {
            'asking_price_per_m2': {'value': _q(own), 'provenance': PROVENANCE},
            'percentile': round(rank),
            'position': 'below_middle_half' if own < p25 else 'above_middle_half' if own > p75 else 'within_middle_half',
            'note': 'Where this asking price per m² sits among comparable asking prices; not a judgement of value.',
        }
    return out


def for_listing(p) -> dict:
    """Context for a listing (its asking price included when it is a priced sale listing)."""
    from apps.markets.services import get_market
    from .models import Property
    info = get_market(p.market_id or p.country) if (p.market_id or p.country) else None
    size = p.size_m2 if p.size_m2 is not None else p._compute_size_m2()
    priced = (p.purpose == Property.Purpose.SALE and p.price_type == Property.PriceType.TOTAL
              and not p.price_on_request and p.price)
    return {'listing_id': str(p.pk),
            **context(country=p.country, city=p.city, currency=p.currency, size_m2=size, category=p.category,
                      subtype=p.subtype, locality=_locality(p.location_ref) if p.location_ref_id else None,
                      asking_price=p.price if priced else None, exclude=p.pk,
                      market_name=getattr(info, 'name', '') or p.country)}
