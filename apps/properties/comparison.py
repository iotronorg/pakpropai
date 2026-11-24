"""
Side-by-side facts of 2–4 listings (T-2041, FR-CMP-001/002, ADR-039).

Only stored facts and arithmetic on them. ``None`` means not specified; it is never
guessed. A hidden price stays hidden in every derived value, including the match.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import prefetch_related_objects
from django.utils import timezone

from apps.markets import fx

from . import listing_search as S
from .criteria import Place, SearchCriteria
from .models import Property
from .public_listings import public_q, safe_images

MIN, MAX = 2, 4
YES, NO, UNKNOWN = 'yes', 'no', 'unknown'
_BASIS = {'total': 'total', 'per_month': 'month', 'per_year': 'month', 'per_sqm': None}


def load(ids, viewer_org=None) -> list[Property]:
    """Visible listings in the order asked; the rest are left out."""
    found = {p.pk: p for p in Property.objects.visible_to(viewer_org).filter(public_q(), pk__in=ids)
             .select_related('organization', 'location_ref', 'market')
             .prefetch_related('amenities')}
    rows = [found[i] for i in dict.fromkeys(ids) if i in found]
    prefetch_related_objects(rows, safe_images())
    return rows


# ── Price ─────────────────────────────────────────────────────────────────────

def _price(p: Property) -> tuple[Decimal | None, str | None]:
    """Comparable amount and basis: the total, or the rent per month."""
    basis = _BASIS.get(p.price_type)
    if p.price_on_request or not p.price or basis is None:
        return None, None
    return (Decimal(p.price) / 12 if p.price_type == 'per_year' else Decimal(p.price)), basis


def _per_m2(p: Property) -> tuple[Decimal | None, str | None]:
    if p.price_type == 'per_sqm':
        if p.price_on_request or not p.price:
            return None, None
        return Decimal(p.price), 'total' if p.purpose == Property.Purpose.SALE else 'unspecified'
    amount, basis = _price(p)
    if amount is None or not p.size_m2:
        return None, None
    return amount / p.size_m2, basis


def _money(amount, basis, currency, target, rates) -> dict | None:
    if amount is None:
        return None
    return {'amount': int(round(amount)), 'currency': currency, 'basis': basis,
            'converted': rates.convert(amount, currency, target) if target != currency else None}


# ── Preference match ──────────────────────────────────────────────────────────

def _unknown(factor: str, p: Property, c: SearchCriteria) -> bool:
    """The listing does not say, so neither yes nor no is true."""
    if factor == 'price':
        return p.price_on_request or not p.price or (p.currency or '').upper() != c.price.currency
    if factor == 'size':
        return p.size_m2 is None
    if factor == 'type':
        return p.category is None or bool(c.subtypes and p.subtype is None)
    field = {'bedrooms': 'bedrooms', 'bathrooms': 'bathrooms', 'furnishing': 'furnished_status',
             'completion': 'construction_status', 'pets_allowed': 'pets_allowed'}.get(factor)
    return field is not None and getattr(p, field) in (None, '')


def _asked(c: SearchCriteria) -> dict[str, dict]:
    """factor → the criteria fields it consists of, for the conditions the person set."""
    out = {}
    if c.purpose:
        out['purpose'] = {'purpose': c.purpose}
    if c.category or c.subtypes:
        out['type'] = {'category': c.category, 'subtypes': c.subtypes}
    if c.location != Place():
        out['location'] = {'location': c.location}
    for name in ('price', 'bedrooms', 'bathrooms', 'size'):
        bounds = getattr(c, name)
        if bounds.min is not None or bounds.max is not None:
            out[name] = {name: bounds}
    for key in c.amenities_required:
        out[f'amenity:{key}'] = {'amenities_required': [key]}
    for key in c.amenities_preferred:
        out[f'amenity:{key}'] = {'amenities_required': [key]}
    for key in c.accessibility:
        out[f'amenity:{key}'] = {'accessibility': [key]}
    for name in ('furnishing', 'completion'):
        if getattr(c, name):
            out[name] = {name: getattr(c, name)}
    if c.pets_allowed is not None:
        out['pets_allowed'] = {'pets_allowed': c.pets_allowed}
    if c.installments:
        out['installments'] = {'installments': True}
    if c.verified_only:
        out['verified'] = {'verified_only': True}
    return out


def match(listings: list[Property], c: SearchCriteria, viewer_org=None) -> dict:
    """
    ``{listing pk: {factor: yes | no | unknown}}``. Each condition uses the search
    engine's own filter, so the answer agrees with the search. Missing amenities
    count as "no": listed amenities are all we know.
    """
    asked = _asked(c)
    out = {p.pk: {f: UNKNOWN if _unknown(f, p, c) else NO for f in asked} for p in listings}
    by_market: dict[str, list] = {}
    for p in listings:
        by_market.setdefault(p.market_id or p.country, []).append(p.pk)
    for factor, fields in asked.items():
        for market, pks in by_market.items():
            if factor == 'location' and market != c.market:
                continue
            one = SearchCriteria.model_construct(**{**SearchCriteria(market=market).__dict__, **fields})
            for pk in S.filtered(one, viewer_org).filter(pk__in=pks).values_list('pk', flat=True):
                if out[pk][factor] == NO:
                    out[pk][factor] = YES
    return out


# ── Table ─────────────────────────────────────────────────────────────────────

def _best(values: dict, pick, same=()) -> str | None:
    """The single best listing; None when a value is unknown, tied, or not comparable."""
    if None in values.values() or len(set(same)) > 1:
        return None
    top = pick(values.values())
    winners = [k for k, v in values.items() if v == top]
    return winners[0] if len(winners) == 1 else None


def compare(listings: list[Property], *, currency: str = '', criteria: SearchCriteria | None = None,
            viewer_org=None) -> dict:
    codes = {(p.currency or '').upper() for p in listings}
    target = (currency or '').upper() or (listings[0].currency or '').upper()
    rates = fx.rates(codes | {target})
    converted_from = codes - {target}
    now = timezone.now()
    matches = match(listings, criteria, viewer_org) if criteria is not None else {}
    shared = set.intersection(*({a.key for a in p.amenities.all() if a.is_active} for p in listings))

    items = []
    for p in listings:
        cur = (p.currency or '').upper()
        amount, basis = _price(p)
        each, each_basis = _per_m2(p)
        keys = {a.key for a in p.amenities.all() if a.is_active}
        hidden = p.price_on_request
        items.append({
            'id': str(p.pk), 'purpose': p.purpose, 'price_on_request': hidden,
            'price': _money(amount, basis, cur, target, rates),
            'price_per_m2': _money(each, each_basis, cur, target, rates),
            'deposit': None if hidden else p.deposit, 'service_charge': None if hidden else p.service_charge,
            'size_m2': float(p.size_m2) if p.size_m2 is not None else None, 'size': S.size_text(p) or None,
            'bedrooms': p.bedrooms, 'bathrooms': p.bathrooms, 'floor': p.floor, 'year_built': p.year_built,
            'parking_spaces': p.parking_spaces, 'furnishing': p.furnished_status or None,
            'completion': p.construction_status or None, 'pets_allowed': p.pets_allowed,
            'installments': p.installment_available, 'city': p.city, 'location': p.location,
            'verification': p.legal_status, 'status': p.status,
            'listed_days': max(0, (now - (p.published_at or p.created_at)).days),
            'amenities_only_here': sorted(keys - shared), 'match': matches.get(p.pk),
        })

    def money(name):
        return {i['id']: i[name] and (i[name]['amount'] if i[name]['currency'] == target else i[name]['converted'])
                for i in items}

    def same(name):
        return [(i['purpose'], i[name]['basis'] if i[name] else None) for i in items]
    return {
        'currency': target, 'fx': rates.describe(converted_from | {target}) if converted_from else None,
        'items': items, 'shared_amenities': sorted(shared),
        'best': {'lowest_price': _best(money('price'), min, same('price')),
                 'lowest_price_per_m2': _best(money('price_per_m2'), min, same('price_per_m2')),
                 'largest': _best({i['id']: i['size_m2'] for i in items}, max),
                 'newest_listing': _best({i['id']: i['listed_days'] for i in items}, min)},
    }
