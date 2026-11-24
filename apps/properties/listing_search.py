"""
Listing search v2 (T-2030, ADR-034): one ``execute`` for REST, the AI tools and
(later) alerts. Hard filters, then a match score computed in SQL from weighted
factors, with reasons taken only from the listing's own fields.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.contrib.postgres.search import SearchQuery
from django.core import signing
from django.db.models import (
    BigIntegerField, Case, Count, DurationField, ExpressionWrapper, F, FloatField, IntegerField, OuterRef, Q,
    Subquery, Value, When,
)
from django.db.models.functions import Cast, Coalesce, Extract, Greatest, Least, Round
from django.utils import timezone

from apps.markets.services import format_price

from .criteria import SearchCriteria
from .models import Amenity, ListingAmenity, Property
from .taxonomy import filter_all_amenities
from .text_search import SEARCH_CONFIG

MAX_LIMIT = 50
SORTS = ('relevance', 'newest', 'price_asc', 'price_desc')
FRESH_DAYS = 90
SCALE = 100_000                     # integer sort key: exact comparison in the cursor
BASE_VERSION = 'v1'
WEIGHTS = {'price': 25, 'location': 20, 'amenities': 15, 'size': 10, 'style': 10,
           'freshness': 10, 'quality': 5, 'verification': 5}
_SALT = 'listing-search-cursor'
_BIG = 2 ** 62


class CursorInvalid(ValueError):
    pass


@dataclass
class Hit:
    listing: Property
    score: int
    reasons: list
    tradeoffs: list


@dataclass
class SearchResult:
    hits: list
    total: int
    criteria: SearchCriteria
    ranking_version: str
    next_cursor: str | None = None
    facets: dict | None = None
    relaxations: list = field(default_factory=list)


# ── Weights ───────────────────────────────────────────────────────────────────

def weights_for(market: str) -> tuple[dict, str]:
    """``Market.settings['ranking_weights']`` over the defaults; invalid values are ignored."""
    from apps.markets.models import Market
    conf = Market.objects.filter(code=market).values_list('settings', flat=True).first() or {}
    own = conf.get('ranking_weights')
    own = {k: v for k, v in (own if isinstance(own, dict) else {}).items()
           if k in WEIGHTS and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100}
    weights = {**WEIGHTS, **own}
    if not own or not any(weights.values()):
        return dict(WEIGHTS), BASE_VERSION
    digest = hashlib.sha256(json.dumps(weights, sort_keys=True).encode()).hexdigest()[:6]
    return weights, f'{BASE_VERSION}-{market}-{digest}'


# ── Hard filters ──────────────────────────────────────────────────────────────

def _m2(value, unit):
    return float(value) * Property._SQM_FACTORS[unit]


def _price_q(price) -> Q:
    """
    Same currency and comparable period. Price-on-request listings never match a
    price filter: matching would reveal the hidden price.
    """
    def band(field_name, factor):
        q = Q()
        if price.min is not None:
            q &= Q(**{f'{field_name}__gte': price.min * factor})
        if price.max is not None:
            q &= Q(**{f'{field_name}__lte': price.max * factor})
        return q

    q = Q(currency=price.currency, price_on_request=False, price__isnull=False)
    if price.period in ('month', 'year'):
        month = (1, 12) if price.period == 'month' else (None, None)
        if month[0]:
            return q & ((Q(price_type='per_month') & band('price', 1)) | (Q(price_type='per_year') & band('price', 12)))
        monthly = Q(price_type='per_month')
        if price.min is not None:
            monthly &= Q(price__gte=-(-price.min // 12))
        if price.max is not None:
            monthly &= Q(price__lte=price.max // 12)
        return q & ((Q(price_type='per_year') & band('price', 1)) | monthly)
    if price.period in ('total', 'sqm'):
        q &= Q(price_type='total' if price.period == 'total' else 'per_sqm')
    return q & band('price', 1)


def _place_q(loc) -> Q:
    """Listings matched to the node or below; unmatched listings by their own text."""
    q = loc.descendants_q('location_ref__path')
    if loc.level == 'country':
        return q | Q(location_ref__isnull=True)
    if loc.level == 'region':
        return q
    names = [loc.name, *(a for a in loc.aliases or [] if isinstance(a, str) and a.strip())]
    text = Q()
    target = 'city' if loc.level == 'city' else 'location'
    for n in names:
        text |= Q(**{f'{target}__iexact' if target == 'city' else f'{target}__icontains': n})
    if loc.level != 'city':
        city = loc.parent
        while city is not None and city.level != 'city':
            city = city.parent
        if city is not None:
            text &= Q(city__iexact=city.name)
    return q | (Q(location_ref__isnull=True) & text)


def _locations(ids):
    from apps.locations.models import Location
    return list(Location.objects.filter(pk__in=ids, is_active=True).select_related('parent__parent__parent'))


def filtered(c: SearchCriteria, viewer_org=None, *, skip: frozenset = frozenset()):
    now = timezone.now()
    qs = (Property.objects.visible_to(viewer_org)
          .filter(Q(market_id=c.market) | Q(market__isnull=True, country=c.market))
          .exclude(expires_at__lte=now))
    if c.purpose and 'purpose' not in skip:
        qs = qs.filter(purpose=c.purpose)
    if c.category:
        qs = qs.filter(category=c.category)
    if c.subtypes and 'subtypes' not in skip:
        qs = qs.filter(subtype__in=c.subtypes)
    place = c.location
    if place.location_ids:
        q = Q()
        for loc in _locations(place.location_ids):
            q |= _place_q(loc)
        qs = qs.filter(q) if q else qs.none()
    if place.free_text and 'area' not in skip:
        for part in [p.strip() for p in place.free_text.split(',') if p.strip()]:
            qs = qs.filter(Q(location__icontains=part) | Q(city__icontains=part))
    if place.geo:
        from apps.geo.search import within_bbox, within_radius
        g = place.geo
        qs = (within_radius(qs, g.lat, g.lng, g.radius_m / 1000.0) if g.type == 'radius'
              else within_bbox(qs, g.min_lng, g.min_lat, g.max_lng, g.max_lat))
    p = c.price
    if (p.min is not None or p.max is not None) and 'price' not in skip:
        qs = qs.filter(_price_q(p))
    for name in ('bedrooms', 'bathrooms'):
        rooms = getattr(c, name)
        if name in skip:
            continue
        if rooms.min is not None:
            qs = qs.filter(**{f'{name}__gte': rooms.min})
        if rooms.max is not None:
            qs = qs.filter(**{f'{name}__lte': rooms.max})
    if 'size' not in skip:
        if c.size.min is not None:
            qs = qs.filter(size_m2__gte=_m2(c.size.min, c.size.unit) * 0.995)
        if c.size.max is not None:
            qs = qs.filter(size_m2__lte=_m2(c.size.max, c.size.unit) * 1.005)
    keys = [*([] if 'amenities' in skip else c.amenities_required), *c.accessibility]
    if keys:
        qs = filter_all_amenities(qs, keys)
    if c.furnishing and 'furnishing' not in skip:
        qs = qs.filter(furnished_status=c.furnishing)
    if c.completion and 'completion' not in skip:
        qs = qs.filter(construction_status=c.completion)
    if c.pets_allowed is not None:
        qs = qs.filter(pets_allowed=c.pets_allowed)
    if c.installments:
        qs = qs.filter(installment_available=True)
    if c.verified_only:
        qs = qs.filter(legal_status=Property.LegalStatus.VERIFIED)
    if c.listed_within_days:
        qs = qs.annotate(_listed=Coalesce('published_at', 'created_at')).filter(
            _listed__gte=now - timedelta(days=c.listed_within_days))
    if c.keywords and 'keywords' not in skip:
        qs = qs.filter(search_tsv=SearchQuery(c.keywords, search_type='plain', config=SEARCH_CONFIG))
    return qs


# ── Score ─────────────────────────────────────────────────────────────────────

def _f(value):
    return Value(float(value), output_field=FloatField())


def _clip(expr):
    return Greatest(_f(0), Least(_f(1), expr))


def _factors(c: SearchCriteria, as_of: datetime) -> dict:
    """Only factors the criteria make meaningful; each is 0–1."""
    out = {}
    if c.price.max:
        # Full marks up to 80 % of the budget, half at the limit.
        monthly = c.price.period == 'month'
        yearly = c.price.period == 'year'
        price = Cast('price', FloatField())
        if monthly:
            price = Case(When(price_type='per_year', then=price / 12.0), default=price, output_field=FloatField())
        elif yearly:
            price = Case(When(price_type='per_month', then=price * 12.0), default=price, output_field=FloatField())
        out['price'] = _clip(_f(1) - (price / float(c.price.max) - _f(0.8)) * 2.5)
    if c.location.geo and c.location.geo.type == 'radius':
        out['location'] = _clip(_f(1) - F('distance_km') * 1000.0 / float(c.location.geo.radius_m))
    elif c.location.location_ids:
        out['location'] = Case(When(location_ref__isnull=False, then=_f(1)), default=_f(0.5),
                               output_field=FloatField())
    if c.amenities_preferred:
        n = (ListingAmenity.objects.filter(property=OuterRef('pk'), amenity__key__in=c.amenities_preferred)
             .order_by().values('property').annotate(n=Count('amenity', distinct=True)).values('n'))
        out['amenities'] = Cast(Coalesce(Subquery(n, output_field=IntegerField()), 0), FloatField()) / float(
            len(c.amenities_preferred))
    if c.size.min is not None and c.size.max is not None:
        lo, hi = _m2(c.size.min, c.size.unit), _m2(c.size.max, c.size.unit)
        half = max((hi - lo) / 2, hi * 0.005)
        size = Cast('size_m2', FloatField())
        out['size'] = _clip(_f(1) - Greatest(size - _f((hi + lo) / 2), _f((hi + lo) / 2) - size) / (2 * half))
    if c.soft_preferences.style:
        styles = c.soft_preferences.style
        out['style'] = sum((Case(When(search_tsv=SearchQuery(s, search_type='plain', config=SEARCH_CONFIG),
                                      then=_f(1)), default=_f(0), output_field=FloatField())
                            for s in styles), _f(0)) / float(len(styles))
    age = ExpressionWrapper(Value(as_of) - Coalesce('published_at', 'created_at'), output_field=DurationField())
    out['freshness'] = _clip(_f(1) - Cast(Extract(age, 'epoch'), FloatField()) / float(FRESH_DAYS * 86400))
    out['quality'] = _clip(Coalesce(Cast('ai_score', FloatField()), _f(50)) / 100.0)
    out['verification'] = Case(When(legal_status='verified', then=_f(1)), When(legal_status='disputed', then=_f(0)),
                               default=_f(0.3), output_field=FloatField())
    return out


def _scored(qs, c, as_of, weights):
    factors = _factors(c, as_of)
    names = [n for n in factors if weights[n] > 0]
    qs = qs.annotate(**{f'f_{n}': factors[n] for n in names})
    total = sum(weights[n] for n in names)
    if not total:
        return qs.annotate(score=_f(0)), []
    return qs.annotate(score=sum((F(f'f_{n}') * _f(weights[n]) for n in names), _f(0)) / float(total)), names


def _sort_key(sort: str, c: SearchCriteria):
    """Integer key; larger first."""
    if sort == 'newest':
        return Cast(Extract(Coalesce('published_at', 'created_at'), 'epoch') * 1000, BigIntegerField())
    if sort in ('price_asc', 'price_desc'):
        # Hidden prices and other currencies go last, in no price order.
        sign = -1 if sort == 'price_asc' else 1
        currency = c.price.currency or _market_currency(c.market)
        return Case(When(price_on_request=False, price__isnull=False, currency=currency,
                         then=F('price') * Value(sign, output_field=BigIntegerField())),
                    default=Value(-_BIG), output_field=BigIntegerField())
    return Cast(Round(F('score') * float(SCALE)), BigIntegerField())


def _market_currency(market):
    from apps.markets.services import get_market
    return get_market(market).default_currency


# ── Cursor ────────────────────────────────────────────────────────────────────

def _digest(c: SearchCriteria, sort: str, version: str, viewer_org) -> str:
    raw = json.dumps([c.model_dump(mode='json'), sort, version, str(getattr(viewer_org, 'pk', viewer_org) or '')],
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _read_cursor(token: str, digest: str):
    try:
        data = signing.loads(token, salt=_SALT, max_age=86400)
        if data['d'] != digest:
            raise CursorInvalid('The cursor belongs to another search.')
        return int(data['k']), str(data['i']), datetime.fromisoformat(data['t'])
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CursorInvalid):
            raise
        raise CursorInvalid('Invalid or expired cursor.') from None


# ── Explanations (only facts stored on the listing) ───────────────────────────

def _labels(keys) -> dict:
    return {a.key: (a.labels or {}).get('en') or a.key.replace('_', ' ') for a in Amenity.objects.filter(key__in=keys)}


def size_text(p: Property) -> str:
    return f'{float(p.size_value):g} {p.size_unit}' if p.size_value and p.size_unit else ''


def price_text(p: Property) -> str:
    if p.price_on_request:
        return 'Price on request'
    suffix = {'per_month': ' per month', 'per_year': ' per year', 'per_sqm': ' per m²'}.get(p.price_type, '')
    return format_price(p.price, p.currency) + (suffix if p.price else '')


def _explain(p, c, names, weights, labels, keys, as_of):
    good, bad = [], []

    def add(weight, text):
        good.append((weight, text))

    f = {n: getattr(p, f'f_{n}') or 0.0 for n in names}
    if c.price.max:
        add(weights['price'] * max(f.get('price', 0), 0.5), f'Within your budget ({price_text(p)})')
        if f.get('price', 1) < 0.6:
            bad.append((weights['price'], 'Near the top of your budget'))
    if c.location.geo and c.location.geo.type == 'radius':
        add(weights['location'] * f.get('location', 0), f'{p.distance_km:.1f} km from the point you chose')
    elif c.location.location_ids or c.location.free_text:
        add(weights['location'] * f.get('location', 0.5), f'In {p.location}, {p.city}')
        if c.location.location_ids and p.location_ref_id is None:
            bad.append((weights['location'] / 2, 'Area taken from the lister\'s text, not matched on our map'))
    if c.bedrooms.min is not None:
        add(20, f'{p.bedrooms} bedroom{"s" if p.bedrooms != 1 else ""} (you asked for {c.bedrooms.min}+)')
    if c.size.min is not None or c.size.max is not None:
        add(weights['size'] * f.get('size', 1), f'{size_text(p)} fits the size you asked for')
    for k in [*c.amenities_required, *c.accessibility]:
        add(18, f'{labels.get(k, k)} listed in amenities')
    for k in c.amenities_preferred:
        if k in keys:
            add(weights['amenities'], f'{labels.get(k, k)} listed in amenities')
        else:
            bad.append((weights['amenities'], f'{labels.get(k, k)} not listed'))
    days = max(0, (as_of - (p.published_at or p.created_at)).days)
    if days <= 7:
        add(weights['freshness'], 'Listed today' if days == 0 else f'Listed {days} day{"s" if days != 1 else ""} ago')
    elif days > 60:
        bad.append((weights['freshness'], f'Listed {days} days ago'))
    if p.legal_status == Property.LegalStatus.VERIFIED:
        add(weights['verification'], 'Documents verified')
    elif p.legal_status == Property.LegalStatus.DISPUTED:
        bad.append((100, 'Legal status is disputed'))
    else:
        bad.append((weights['verification'], 'Documents not verified'))
    if p.status == Property.Status.UNDER_OFFER:
        bad.append((90, 'Already under offer'))
    if p.price_on_request:
        bad.append((30, 'Price on request'))
    top = lambda items, n: [t for _, t in sorted(items, key=lambda x: -x[0])[:n]]     # noqa: E731 - stable sort
    return top(good, 3), top(bad, 1)


def summary(p: Property) -> dict:
    """Public listing facts shared by search results, chat cards and the AI tools."""
    return {
        'id': str(p.pk), 'ref_no': p.ref_no, 'title': p.title, 'city': p.city, 'location': p.location,
        'price': None if p.price_on_request else p.price, 'currency': p.currency, 'price_type': p.price_type,
        'purpose': p.purpose, 'category': p.category, 'subtype': p.subtype, 'size': size_text(p),
        'bedrooms': p.bedrooms, 'bathrooms': p.bathrooms,
        'verified': p.legal_status == Property.LegalStatus.VERIFIED,
    }


# ── Execute ───────────────────────────────────────────────────────────────────

def facets(qs) -> dict:
    qs = qs.order_by()

    def counts(name):
        return {str(k): n for k, n in qs.filter(**{f'{name}__isnull': False}).values_list(name)
                .annotate(n=Count('pk', distinct=True)).order_by('-n', name)[:15]}
    amenities = dict(ListingAmenity.objects.filter(property__in=qs.values('pk')).order_by().values_list('amenity__key')
                     .annotate(n=Count('property', distinct=True)).order_by('-n', 'amenity__key')[:15])
    return {'purpose': counts('purpose'), 'category': counts('category'), 'subtype': counts('subtype'),
            'bedrooms': counts('bedrooms'), 'amenities': amenities}


def relaxations(c: SearchCriteria, viewer_org=None) -> list[dict]:
    """For an empty result: how many listings match when one condition is dropped."""
    active = []
    if c.price.min is not None or c.price.max is not None:
        active.append('price')
    active += [n for n in ('bedrooms', 'bathrooms') if getattr(c, n).min is not None or getattr(c, n).max is not None]
    if c.size.min is not None or c.size.max is not None:
        active.append('size')
    for name, on in (('amenities', c.amenities_required), ('subtypes', c.subtypes), ('furnishing', c.furnishing),
                     ('completion', c.completion), ('keywords', c.keywords),
                     ('area', c.location.free_text and (c.location.location_ids or c.location.geo))):
        if on:
            active.append(name)
    out = [{'drop': n, 'count': filtered(c, viewer_org, skip=frozenset({n})).count()} for n in active]
    return sorted((r for r in out if r['count']), key=lambda r: -r['count'])[:3]


def execute(c: SearchCriteria, *, viewer_org=None, sort: str = 'relevance', limit: int = 20, cursor: str = '',
            with_facets: bool = False) -> SearchResult:
    """``c`` must come from ``criteria.prepare``."""
    if sort not in SORTS:
        raise ValueError('sort')
    limit = max(1, min(int(limit), MAX_LIMIT))
    weights, version = weights_for(c.market)
    digest = _digest(c, sort, version, viewer_org)
    last, as_of = None, timezone.now()
    if cursor:
        key, pk, as_of = _read_cursor(cursor, digest)
        last = (key, pk)

    base = filtered(c, viewer_org)
    total = base.count()
    qs, names = _scored(base, c, as_of, weights)
    qs = qs.annotate(sort_key=_sort_key(sort, c))
    if last:
        qs = qs.filter(Q(sort_key__lt=last[0]) | Q(sort_key=last[0], pk__gt=last[1]))
    rows = list(qs.prefetch_related('amenities').order_by('-sort_key', 'pk')[:limit + 1])
    more, rows = len(rows) > limit, rows[:limit]

    labels = _labels([*c.amenities_required, *c.amenities_preferred, *c.accessibility])
    hits = []
    for p in rows:
        reasons, tradeoffs = _explain(p, c, names, weights, labels, {a.key for a in p.amenities.all()}, as_of)
        hits.append(Hit(p, round((p.score or 0) * 100), reasons, tradeoffs))
    next_cursor = None
    if more:
        next_cursor = signing.dumps({'k': rows[-1].sort_key, 'i': str(rows[-1].pk), 't': as_of.isoformat(),
                                     'd': digest}, salt=_SALT)
    return SearchResult(hits, total, c, version, next_cursor, facets(base) if with_facets else None,
                        relaxations(c, viewer_org) if not total else [])


_POSITION = re.compile(r'#?(\d{1,2})')


def find(ref: str, viewer_org=None, last_results=()) -> Property | None:
    """A visible listing by id, reference number, or position in the last results ("2")."""
    import uuid
    ref = str(ref or '').strip()
    qs = Property.objects.visible_to(viewer_org).prefetch_related('amenities')
    if m := _POSITION.fullmatch(ref):
        n = int(m.group(1))
        if not 1 <= n <= len(last_results):
            return None
        ref = str(last_results[n - 1])
    try:
        return qs.filter(pk=uuid.UUID(ref)).first()
    except ValueError:
        return qs.filter(ref_no__iexact=ref).first() if ref else None
