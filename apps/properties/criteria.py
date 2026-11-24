"""
``SearchCriteria`` (T-2030, ADR-034, AI spec §4.1): the one description of a
search, used by REST, the AI tools and the conversation state.

:func:`prepare` validates it against the market and the taxonomies. It returns
the normalised criteria, or raises :class:`CriteriaInvalid` (field errors) or
:class:`LocationAmbiguous` (candidates to choose from).
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .taxonomy import ALL_SUBTYPES, CATEGORIES, SUBTYPES, active_amenity_keys

MAX_MONEY = 10 ** 13
MAX_SIZE = 10 ** 8


class _M(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    @model_validator(mode='after')
    def _range(self):
        lo, hi = getattr(self, 'min', None), getattr(self, 'max', None)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError('min must not exceed max')
        return self


class Geo(_M):
    type: Literal['radius', 'bbox']
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    radius_m: Optional[int] = Field(None, ge=100, le=100_000)
    min_lng: Optional[float] = Field(None, ge=-180, le=180)
    min_lat: Optional[float] = Field(None, ge=-90, le=90)
    max_lng: Optional[float] = Field(None, ge=-180, le=180)
    max_lat: Optional[float] = Field(None, ge=-90, le=90)

    @model_validator(mode='after')
    def _complete(self):
        need = ('lat', 'lng', 'radius_m') if self.type == 'radius' else ('min_lng', 'min_lat', 'max_lng', 'max_lat')
        if any(getattr(self, n) is None for n in need):
            raise ValueError(f'{self.type} needs {", ".join(need)}')
        if self.type == 'bbox' and (self.min_lat > self.max_lat or self.min_lng > self.max_lng):
            raise ValueError('bbox min must not exceed max')
        return self


class Place(_M):
    location_ids: list[int] = Field(default_factory=list, max_length=20)
    free_text: str = Field('', max_length=150)
    geo: Optional[Geo] = None


class Price(_M):
    min: Optional[int] = Field(None, ge=0, le=MAX_MONEY)
    max: Optional[int] = Field(None, gt=0, le=MAX_MONEY)
    currency: str = Field('', max_length=3)
    period: Optional[Literal['total', 'month', 'year', 'sqm']] = None


class Rooms(_M):
    min: Optional[int] = Field(None, ge=0, le=50)
    max: Optional[int] = Field(None, ge=0, le=50)


class Size(_M):
    min: Optional[float] = Field(None, ge=0, le=MAX_SIZE)
    max: Optional[float] = Field(None, gt=0, le=MAX_SIZE)
    unit: str = Field('', max_length=10)


class Soft(_M):
    style: list[str] = Field(default_factory=list, max_length=10)
    notes: str = Field('', max_length=300)


class Unsupported(_M):
    text: str = Field(max_length=100)
    reason: Literal['subjective_or_protected', 'no_data']


class SearchCriteria(_M):
    market: str = Field('', max_length=2)
    purpose: Optional[Literal['sale', 'rent_long', 'rent_short', 'lease_commercial']] = None
    any_purpose: bool = False                  # the user said buying or renting does not matter
    category: Optional[str] = None
    subtypes: list[str] = Field(default_factory=list, max_length=10)
    location: Place = Field(default_factory=Place)
    price: Price = Field(default_factory=Price)
    bedrooms: Rooms = Field(default_factory=Rooms)
    bathrooms: Rooms = Field(default_factory=Rooms)
    size: Size = Field(default_factory=Size)
    amenities_required: list[str] = Field(default_factory=list, max_length=20)
    amenities_preferred: list[str] = Field(default_factory=list, max_length=20)
    accessibility: list[str] = Field(default_factory=list, max_length=10)
    furnishing: Optional[Literal['furnished', 'unfurnished', 'semi_furnished']] = None
    completion: Optional[Literal['builder', 'ready', 'under_construction']] = None
    pets_allowed: Optional[bool] = None
    installments: Optional[bool] = None
    verified_only: bool = False
    listed_within_days: Optional[int] = Field(None, ge=1, le=365)
    keywords: str = Field('', max_length=150)
    soft_preferences: Soft = Field(default_factory=Soft)
    unsupported_requests: list[Unsupported] = Field(default_factory=list, max_length=10)
    missing_required: list[str] = Field(default_factory=list, max_length=5)
    confidence: dict[str, float] = Field(default_factory=dict, max_length=20)

    @field_validator('market')
    @classmethod
    def _upper(cls, v):
        return v.strip().upper()

    @field_validator('category')
    @classmethod
    def _category(cls, v):
        if v is not None and v not in CATEGORIES:
            raise ValueError(f'one of {", ".join(CATEGORIES)}')
        return v

    @field_validator('subtypes')
    @classmethod
    def _subtypes(cls, v):
        if bad := [s for s in v if s not in ALL_SUBTYPES]:
            raise ValueError(f'unknown: {", ".join(bad)}')
        return v

    @field_validator('amenities_required', 'amenities_preferred', 'accessibility')
    @classmethod
    def _keys(cls, v):
        return list(dict.fromkeys(str(k).strip().lower()[:50] for k in v if str(k).strip()))


class CriteriaInvalid(ValueError):
    def __init__(self, errors: dict):
        super().__init__(str(errors))
        self.errors = errors


class LocationAmbiguous(ValueError):
    def __init__(self, text: str, candidates: list[dict]):
        super().__init__(text)
        self.text, self.candidates = text, candidates


def parse(data) -> SearchCriteria:
    try:
        return SearchCriteria.model_validate(data)
    except ValidationError as exc:
        errors = {}
        for e in exc.errors(include_url=False, include_input=False, include_context=False):
            errors.setdefault('.'.join(str(p) for p in e['loc']) or 'criteria', []).append(e['msg'])
        raise CriteriaInvalid(errors) from None


# ── Policy: requests that must never become a filter (AI spec §8.5) ───────────

_SUBJECTIVE = re.compile(
    r'\b(?:safe(?:st|r)?|secure|low[- ]crime|crime[- ]free|family[- ]friendly|famil(?:y|ies) only|'
    r'(?:good|decent|respectable|educated|nice) (?:people|neighbou?rs|community|neighbou?rhood)|'
    r'(?:muslim|christian|hindu|sikh|jewish|shia|sunni|ahmadi|punjabi|pathan|pashtun|sindhi|muhajir|arab|'
    r'indian|white|black|asian|western|european)s?(?:[- ]only)?\s+(?:area|neighbou?rhood|community|society|'
    r'neighbou?rs|people|families|building|only)|no (?:bachelors?|kids|children|foreigners))\b'
    r'|mehfooz|محفوظ', re.I)
_PROXIMITY = re.compile(
    r'\b(?:near|close to|walking distance (?:to|from)|next to)\s+(?:the\s+|a\s+|an\s+)?'
    r'(?:public transport(?:ation)?|metro|station|bus(?: stop)?|school|hospital|mosque|park|mall|market|airport)s?\b', re.I)


def unsupported(*texts: str) -> list[Unsupported]:
    out = {}
    for text in texts:
        for rx, reason in ((_SUBJECTIVE, 'subjective_or_protected'), (_PROXIMITY, 'no_data')):
            for m in rx.finditer(text or ''):
                out.setdefault(m.group(0).lower(), Unsupported(text=m.group(0)[:100], reason=reason))
    return list(out.values())[:10]


def _strip_policy(text: str) -> str:
    return ' '.join(_PROXIMITY.sub(' ', _SUBJECTIVE.sub(' ', text or '')).split())


# ── Location text ─────────────────────────────────────────────────────────────

def label(loc) -> str:
    names, node = [loc.name], loc.parent
    while node is not None and node.level in ('city', 'locality'):
        names.append(node.name)
        node = node.parent
    return ', '.join(names)


def _match(market: str, text: str) -> list:
    """Active nodes whose name or alias is exactly ``text``; a single city wins."""
    from apps.locations.models import Location
    from apps.locations.services import normalise
    key = normalise(text)
    if not key:
        return []
    base = Location.objects.filter(market_id=market, is_active=True).select_related('parent__parent__parent')
    hits = list(base.filter(match_key=key)[:20])
    if not hits:
        hits = [x for x in base.filter(level='city').exclude(aliases=[])
                if key in {normalise(a) for a in x.aliases}]
    cities = [h for h in hits if h.level == 'city']
    if len(cities) == 1:
        return cities
    return [h for h in hits if h.is_verified] or hits


def _split_city(market: str, text: str):
    """'DHA Phase 5, Lahore' / 'DHA Phase 5 Lahore' → (city node, 'DHA Phase 5')."""
    parts = [p.strip() for p in text.split(',') if p.strip()]
    splits = [(parts[-1], ', '.join(parts[:-1])), (parts[0], ', '.join(parts[1:]))] if len(parts) > 1 else []
    words = text.replace(',', ' ').split()
    for n in (2, 1):
        if len(words) > n:
            splits += [(' '.join(words[-n:]), ' '.join(words[:-n])), (' '.join(words[:n]), ' '.join(words[n:]))]
    for city, rest in splits:
        hits = _match(market, city)
        if len(hits) == 1 and hits[0].level == 'city':
            return hits[0], rest
    return None, text


def resolve_place(market: str, text: str):
    """
    ``(node or None, remaining text)``. Raises LocationAmbiguous when several
    places share the name. Unknown places stay as text.
    """
    from apps.locations.services import match_location, normalise
    hits = _match(market, text)
    if len(hits) == 1:
        return hits[0], ''
    if len(hits) > 1:
        raise LocationAmbiguous(text, [{'id': h.pk, 'label': label(h), 'level': h.level} for h in hits[:8]])
    city, rest = _split_city(market, text)
    if city is None:
        return None, text
    node = match_location(market, city.name, rest, create=False) or city
    names = {node.match_key, *(normalise(a) for a in node.aliases or [])}
    if node.level == 'sub_locality' and node.parent is not None:      # "Society Phase 5"
        names |= {f'{node.parent.match_key} {n}' for n in names}
    exact = normalise(rest) in names
    return node, '' if exact else rest


# ── Preparation ───────────────────────────────────────────────────────────────

_PERIOD = {'sale': 'total', 'rent_long': 'month', 'rent_short': 'month', 'lease_commercial': 'month'}


def prepare(c: SearchCriteria, *, default_market: str = '') -> tuple[SearchCriteria, list[str]]:
    """Validated copy and the list of assumed fields (defaults the user did not state)."""
    from apps.locations.models import Location
    from apps.markets.models import Market
    from apps.markets.services import default_market_code, get_market
    from .models import Property

    c = c.model_copy(deep=True)
    errors, assumed = {}, []
    c.market = c.market or (default_market or '').upper() or default_market_code()
    info = get_market(c.market)
    if info is None or Market.objects.filter(code=c.market, status=Market.Status.INACTIVE).exists():
        raise CriteriaInvalid({'market': ['market.unsupported']})

    if c.category and (bad := [s for s in c.subtypes if s not in SUBTYPES[c.category]]):
        errors['subtypes'] = [f'Not {c.category}: {", ".join(bad)}']

    if c.price.min is not None or c.price.max is not None:
        if not c.price.currency:
            c.price.currency = info.default_currency
            assumed.append('price.currency')
        c.price.currency = c.price.currency.upper()
        if c.price.currency not in (info.currencies or [info.default_currency]):
            errors['price.currency'] = ['price.currency.unsupported']
        if c.price.period is None:
            c.price.period = _PERIOD.get(c.purpose)
            if c.price.period == 'month':
                assumed.append('price.period')
    if c.size.min is not None or c.size.max is not None:
        if not c.size.unit:
            c.size.unit = info.default_area_unit
            assumed.append('size.unit')
        if c.size.unit not in Property._SQM_FACTORS:
            errors['size.unit'] = [f'One of {", ".join(Property._SQM_FACTORS)}']

    known = active_amenity_keys([*c.amenities_required, *c.amenities_preferred, *c.accessibility])
    unknown = []
    for name in ('amenities_required', 'amenities_preferred', 'accessibility'):
        keys = getattr(c, name)
        unknown += [k for k in keys if k not in known]
        setattr(c, name, [k for k in keys if k in known])
    c.amenities_preferred = [k for k in c.amenities_preferred if k not in c.amenities_required]
    if unknown:
        c.soft_preferences.notes = ', '.join(dict.fromkeys(
            [*filter(None, [c.soft_preferences.notes]), *unknown]))[:300]

    # Subjective or protected wording never reaches a filter or the ranking.
    found = unsupported(c.keywords, c.location.free_text, c.soft_preferences.notes, *c.soft_preferences.style)
    c.unsupported_requests = list({u.text.lower(): u for u in [*c.unsupported_requests, *found]}.values())[:10]
    c.keywords = _strip_policy(c.keywords)
    c.location.free_text = _strip_policy(c.location.free_text)
    c.soft_preferences.style = [s for s in map(_strip_policy, c.soft_preferences.style) if s]

    if c.location.location_ids:
        ids = set(Location.objects.filter(pk__in=c.location.location_ids, market_id=c.market, is_active=True)
                  .values_list('pk', flat=True))
        if bad := [i for i in c.location.location_ids if i not in ids]:
            errors['location.location_ids'] = [f'Unknown: {", ".join(map(str, bad))}']
    if errors:
        raise CriteriaInvalid(errors)
    if c.location.free_text and not c.location.location_ids:
        node, rest = resolve_place(c.market, c.location.free_text)
        if node is not None:
            c.location.location_ids, c.location.free_text = [node.pk], rest

    c.missing_required = []
    if not (c.location.location_ids or c.location.free_text or c.location.geo):
        c.missing_required.append('location')
    if c.purpose is None and not c.any_purpose:
        c.missing_required.append('purpose')
    return c, assumed
