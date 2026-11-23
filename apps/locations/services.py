"""
Location matching (T-1060a, ADR-024).

``match_location(market, city, location)`` maps a listing's free-text city and
location to a node of the hierarchy:

1. City: name or alias of a city in the market (normalised: case, punctuation,
   ``ph``→``phase`` etc.). Unknown city → a new *unverified* city under the
   country (review queue), when ``create`` is set.
2. Location text under that city, most specific first:
   a. exact name/alias of a locality or sub-locality of the city;
   b. the longest locality whose name starts the text ("DHA" in "DHA Phase 5"):
      the rest is matched against its sub-localities, else a new unverified
      sub-locality is created under it;
   c. otherwise a new unverified locality under the city.
   Empty text → the city itself.

Nothing here calls an external service; geocoding is T-1060c.
"""
from __future__ import annotations

import re
import unicodedata

from django.db import IntegrityError, transaction

_SYNONYMS = {
    'ph': 'phase', 'phs': 'phase', 'blk': 'block', 'bl': 'block', 'sec': 'sector',
    'sect': 'sector', 'st': 'street', 'rd': 'road', 'ext': 'extension', 'twn': 'town',
    'soc': 'society', 'no': '', 'number': '', 'nr': '',
}
_TOKEN_RE = re.compile(r'[^\W_]+', re.UNICODE)
MAX_TEXT = 150


def normalise(text: str | None) -> str:
    """'D.H.A Ph-05, Block #C' → 'dha phase 5 block c' (stable matching key).

    Runs of two or more single letters are joined, so dotted acronyms
    (D.H.A., P.E.C.H.S.) match their plain spelling.
    """
    text = unicodedata.normalize('NFKC', (text or '')[:MAX_TEXT]).lower()
    out, letters = [], []
    for tok in _TOKEN_RE.findall(text):
        tok = _SYNONYMS.get(tok, tok)
        if tok.isdigit():
            tok = str(int(tok))
        if len(tok) == 1 and tok.isalpha():
            letters.append(tok)
            continue
        if letters:
            out.append(''.join(letters))
            letters = []
        if tok:
            out.append(tok)
    if letters:
        out.append(''.join(letters))
    return ' '.join(out)


def _keys(row) -> set[str]:
    return {row['match_key']} | {normalise(a) for a in (row.get('aliases') or [])}


def _children(parent_id, levels):
    from .models import Location
    return list(Location.objects.filter(parent_id=parent_id, level__in=levels, is_active=True)
                .values('id', 'match_key', 'aliases', 'level', 'is_verified'))


def _find(rows, key):
    """Prefer verified rows on a tie."""
    hits = [r for r in rows if key in _keys(r)]
    hits.sort(key=lambda r: not r['is_verified'])
    return hits[0]['id'] if hits else None


def country_node(market_code: str):
    from .models import Location
    return Location.objects.filter(market_id=market_code, level='country', parent__isnull=True).first()


def _create(parent, level, name):
    """New unverified node (idempotent on the slug: a concurrent match returns the same row)."""
    from django.utils.text import slugify
    from .models import Location
    name = ' '.join(name.split())[:MAX_TEXT]
    slug = slugify(name, allow_unicode=True)[:160] or 'x'
    existing = Location.objects.filter(parent=parent, slug=slug).first()
    if existing:
        return existing
    try:
        with transaction.atomic():
            return Location.objects.create(market_id=parent.market_id, parent=parent, level=level,
                                           name=name, slug=slug, is_verified=False,
                                           source=Location.Source.LISTING)
    except IntegrityError:
        return Location.objects.get(parent=parent, slug=slug)


def match_location(market_code: str | None, city: str | None, location: str | None = '',
                   *, create: bool = True):
    """Best ``Location`` for the text, or ``None`` (no market tree / no city)."""
    from .models import Location
    if not market_code or not (city or '').strip():
        return None
    country = country_node(market_code)
    if country is None:
        return None
    city_key = normalise(city)
    if not city_key:
        return None
    cities = list(Location.objects.filter(market_id=market_code, level='city', is_active=True)
                  .values('id', 'match_key', 'aliases', 'level', 'is_verified'))
    city_id = _find(cities, city_key)
    if city_id is None:
        if not create:
            return None
        city_node = _create(country, 'city', city.strip().title())
    else:
        city_node = Location.objects.get(pk=city_id)

    text_key = normalise(location)
    if not text_key or text_key == city_key:
        return city_node

    localities = _children(city_node.pk, ['locality', 'sub_locality'])
    if (hit := _find(localities, text_key)) is not None:
        return Location.objects.get(pk=hit)
    # Sub-localities one level further down (e.g. city → society → phase).
    subs = []
    for loc in (r for r in localities if r['level'] == 'locality'):
        subs.extend(_children(loc['id'], ['sub_locality']))
    if (hit := _find(subs, text_key)) is not None:
        return Location.objects.get(pk=hit)

    tokens = text_key.split()
    best = None
    for r in localities:
        if r['level'] != 'locality':
            continue
        for k in _keys(r):
            kt = k.split()
            if kt and tokens[:len(kt)] == kt and len(kt) < len(tokens):
                if best is None or len(kt) > best[1] or (len(kt) == best[1] and r['is_verified']):
                    best = (r['id'], len(kt))
    if best is not None:
        parent = Location.objects.get(pk=best[0])
        rest = ' '.join(tokens[best[1]:])
        if (hit := _find(_children(parent.pk, ['sub_locality']), rest)) is not None:
            return Location.objects.get(pk=hit)
        if not create:
            return parent
        raw_rest = _raw_remainder(location, best[1])
        return _create(parent, 'sub_locality', raw_rest or rest)
    return _create(city_node, 'locality', location.strip()) if create else city_node


def _raw_remainder(text: str, n_tokens: int) -> str:
    """Original spelling after the first ``n_tokens`` normalised tokens (best effort)."""
    raw = [t for t in _TOKEN_RE.findall(text or '') if normalise(t)]
    return ' '.join(raw[n_tokens:]).title() if len(raw) > n_tokens else ''


def merge(source, target) -> int:
    """
    Merge ``source`` into ``target`` (review queue): the source name becomes an
    alias, listings and child nodes move to the target, the source is
    deactivated. Returns the number of listings moved.
    """
    from apps.properties.models import Property
    from .models import Location
    if source.pk == target.pk or target.path.startswith(source.path + '/'):
        raise ValueError('Cannot merge a location into itself or its descendant.')
    if source.market_id != target.market_id:
        raise ValueError('Locations belong to different markets.')
    with transaction.atomic():
        aliases = list(target.aliases or [])
        for name in [source.name, *(source.aliases or [])]:
            if normalise(name) not in _keys({'match_key': target.match_key, 'aliases': aliases}):
                aliases.append(name)
        target.aliases = aliases
        target.save(update_fields=['aliases'])
        moved = Property.objects.filter(location_ref=source).update(location_ref=target)
        for child in Location.objects.filter(parent=source):
            if Location.objects.filter(parent=target, slug=child.slug).exists():
                merge(child, Location.objects.get(parent=target, slug=child.slug))
            else:
                child.parent = target
                child.save()
        source.is_active = False
        source.slug = f'{source.slug}-merged-{source.pk}'[:160]
        source.save()
    return moved
