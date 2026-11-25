"""
Grounding checker v2 (T-2013, ADR-032, FR-AI-007, AI spec §3.1/§8.2).

Every listing fact in a model-written reply must match a listing the platform
gave the model: a listing returned by a tool this turn, or by the last search
of the same conversation (``LAST_TTL``). Facts are re-read from the database.

* The reply is split into blocks; a block naming one listing is checked against
  that listing, any other block against all sources.
* Checked: price and currency, size, bedrooms, bathrooms, amenities,
  instalments, city, verification. Deterministic, no model involved.
* Numbers that are not listing facts stay allowed: ones from non-listing tools
  (tax, loan), the system prompt (market knowledge), and the user's own budget.
* With no source at all, a line that reads like a listing (price plus size or
  rooms) is a violation: the listing was invented.

The runtime asks the model to correct itself once, then sends :func:`fallback`,
which is built from the sources only.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.cache import cache

from apps.markets.pk import CURRENCY as PK_CURRENCY

from .compliance_filters import _PREFIX_TO_ISO, _WORD_TO_CURRENCY

logger = logging.getLogger(__name__)

LAST_TTL = 30 * 60
PRICE_TOLERANCE = Decimal('0.01')      # "1.26 crore" for 12,550,000
SIZE_TOLERANCE = 0.02


# ── Sources ───────────────────────────────────────────────────────────────────

@dataclass
class Listing:
    id: str
    title: str = ''
    city: str = ''
    location: str = ''
    price: int | None = None
    currency: str = ''
    size_m2: float | None = None
    size_label: str = ''
    bedrooms: int | None = None
    bathrooms: int | None = None
    amenities: set | None = None          # None = unknown (third-party result)
    installments: bool | None = None
    verified: bool | None = None
    text: str = ''                        # the lister's own words
    amounts: list = field(default_factory=list)   # (amount, currency) derived for this listing only (comparison)

    @property
    def words(self) -> str:
        return f'{self.title}\n{self.text}'

    def names(self) -> list[str]:
        return [n for n in (_norm(self.title),) if len(n) >= 6]


def _norm(text: str) -> str:
    return re.sub(r'[^\w]+', ' ', (text or '').lower()).strip()


def _rows(result) -> list[dict]:
    rows = result.get('properties') if isinstance(result, dict) else None
    return [r for r in rows or () if isinstance(r, dict)]


def collect(rows: list[dict]) -> list[Listing]:
    """Tool rows → facts; own listings are re-read so the check uses current data."""
    from apps.properties.models import Property
    from apps.markets.services import default_currency
    import uuid
    own = {}
    for r in rows:
        if r.get('source') == 'pakprop':
            try:
                own[str(uuid.UUID(str(r.get('id'))))] = r
            except ValueError:
                continue
    amounts: dict[str, list] = {}
    for r in rows:
        amounts.setdefault(str(r.get('id') or ''), []).extend(r.get('amounts') or ())
    out = {}
    for p in Property.objects.filter(pk__in=own, is_active=True).prefetch_related('amenities'):
        out[str(p.pk)] = Listing(
            id=str(p.pk), title=p.title, city=p.city, location=p.location,
            price=None if p.price_on_request else p.price, currency=(p.currency or '').upper(),
            size_m2=float(p.size_m2) if p.size_m2 else None,
            size_label=f'{float(p.size_value):g} {p.size_unit}' if p.size_value and p.size_unit else '',
            bedrooms=p.bedrooms, bathrooms=p.bathrooms, amenities={a.key for a in p.amenities.all()},
            installments=p.installment_available,
            verified=p.legal_status == Property.LegalStatus.VERIFIED, text=p.description or '',
            amounts=[] if p.price_on_request else amounts.get(str(p.pk), []))
    for r in rows:
        key = str(r.get('id') or '')
        if r.get('source') == 'pakprop' or not key or key in out:
            continue
        try:
            m2 = float(r.get('area_marla')) * Property._SQM_FACTORS['marla']
        except (TypeError, ValueError):
            m2 = None
        out[key] = Listing(
            id=key, title=r.get('title') or '', city=r.get('city') or '', location=r.get('location') or '',
            price=r.get('price') or None, currency=(r.get('currency') or default_currency()).upper(),
            size_m2=m2, size_label=f"{r['area_marla']} marla" if m2 else '',
            bedrooms=r.get('bedrooms'), bathrooms=r.get('bathrooms'), verified=False)
    return list(out.values())


def _last_key() -> str:
    from ._tools_context import _ctx_org, _ctx_phone
    from .confirmation import conversation_key
    phone = _ctx_phone.get()
    return f'ai:grounding:{conversation_key(phone, _ctx_org.get())}' if phone else ''


_KEEP = ('id', 'source', 'title', 'city', 'location', 'price', 'currency', 'area_marla',
         'bedrooms', 'bathrooms', 'amounts')


class Evidence:
    """What the model was given during one turn."""

    def __init__(self, message: str, history: list, system: str):
        self.user_text = '\n'.join([message or ''] + [
            h.get('text') or '' for h in history or () if h.get('role') != 'model'])
        self.system = system or ''
        self.rows: list[dict] = []
        self.numbers: set[Decimal] = set()
        self._listings: list[Listing] | None = None

    def add(self, result) -> None:
        rows = _rows(result)
        if rows:
            self.rows += [{k: r.get(k) for k in _KEEP} for r in rows]
            for r in rows:                     # recorded amounts other than the price
                self._collect_numbers([r.get('deposit'), r.get('service_charge')])
            self._listings = None
        else:
            self._collect_numbers(result)

    def _collect_numbers(self, value) -> None:
        if isinstance(value, dict):
            for v in value.values():
                self._collect_numbers(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                self._collect_numbers(v)
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, (int, float, Decimal)):
            self.numbers.add(Decimal(str(value)))
        elif isinstance(value, str):
            self.numbers |= {a for a, _ in money(value)}

    def listings(self) -> list[Listing]:
        if self._listings is None:
            key = _last_key()
            rows = self.rows
            if rows:
                if key:
                    cache.set(key, rows, LAST_TTL)
            elif key:
                rows = cache.get(key) or []
            self._listings = collect(rows)
        return self._listings


# ── Claim extraction ──────────────────────────────────────────────────────────

_UNITS = {'crore': 10**7, 'cr': 10**7, 'karor': 10**7, 'lakh': 10**5, 'lac': 10**5, 'lacs': 10**5,
          'lakhs': 10**5, 'crores': 10**7, 'million': 10**6, 'mn': 10**6, 'billion': 10**9,
          'thousand': 10**3, 'k': 10**3, 'کروڑ': 10**7, 'لاکھ': 10**5, 'مليون': 10**6}
_CUR = {**_PREFIX_TO_ISO, **_WORD_TO_CURRENCY, 'rupee': PK_CURRENCY, 'rupees': PK_CURRENCY, 'روپے': PK_CURRENCY}
_MONEY_RE = re.compile(
    r'(?:(?P<pre>(?<!\w)(?:pkr|rs|₨|aed|usd|gbp|eur|sar|inr|cad|aud|sgd)|[$£€₹])\.?\s*)?'
    r'(?<![\w.,])(?P<num>\d(?:[\d,]*\d)?(?:\.\d+)?)'
    r'(?:\s*(?P<unit>crores?|cr|karor|lakhs?|lacs?|million|mn|billion|thousand|k|کروڑ|لاکھ|مليون)(?!\w))?'
    r'(?:\s*(?P<post>pkr|rs|aed|usd|gbp|eur|sar|inr|cad|aud|sgd|rupees|dirhams?|روپے|درهم)(?!\w))?'
    r'(?P<tail>\s*(?:%|marla|kanal|sq\b|sqft|sqm|sq\.|m²))?', re.I)
_PRE_FIX = re.compile(r'(?<!\w)(pkr|rs\.?|aed|usd|gbp|eur|sar|inr|cad|aud|sgd)(?=\d)', re.I)


def money(text: str) -> list[tuple[Decimal, str]]:
    """Every amount of money in the text: ``(amount, ISO currency or '')``."""
    out = []
    text = _PRE_FIX.sub(r'\1 ', text or '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    # "2 crore 50 lakh" is one amount
    text = re.sub(r'(\d+(?:\.\d+)?)\s*crores?\s+(?:and\s+)?(\d+(?:\.\d+)?)\s*(?:lakhs?|lacs?)\b',
                  lambda m: f'{Decimal(m[1]) + Decimal(m[2]) / 100} crore', text, flags=re.I)
    for m in _MONEY_RE.finditer(text):
        if m['tail']:
            continue
        unit = (m['unit'] or '').lower()
        cur = _CUR.get((m['pre'] or m['post'] or '').lower().rstrip('.'), '')
        if not cur and (not unit or unit in ('k', 'thousand')):
            continue
        try:
            amount = Decimal(m['num'].replace(',', '')) * _UNITS.get(unit, 1)
        except ArithmeticError:
            continue
        if amount >= 1000:
            out.append((amount, cur))
    return out


_SIZE_RE = re.compile(
    r'(?<![\w.,])(\d[\d,]*(?:\.\d+)?)\s*-?\s*(marlas?|kanals?|sq\.?\s*f(?:ee|oo)?t|square\s+f(?:ee|oo)t|'
    r'sq\.?\s*m(?:et(?:er|re)s?)?\b|square\s+met(?:er|re)s?|m²|مرلہ|کنال)', re.I)
_M_RE = re.compile(r'(?<![\w.,])(\d+(?:\.\d+)?)(M)(?![\w²])')      # "10M" = 10 marla in listing titles
_BED_RE = re.compile(r'(?<![\w.])(\d{1,2})\s*-?\s*(?:bed(?:room)?s?|br|bhk|kamr[ae]y?|کمر[ےہ]|کمروں|بیڈ\s*رومز?|بیڈ)\b', re.I)
_BATH_RE = re.compile(r'(?<![\w.])(\d{1,2})\s*-?\s*(?:bath(?:room)?s?|washrooms?|ghusal\s*khan[ae]y?|باتھ(?:\s*رومز?)?|غسل\s*خان[ےہ])\b', re.I)
_INSTALL_RE = re.compile(r'\b(?:instal?lments?|payment plan|qist|aqsaat|taqseet)\b|قسط|أقساط|تقسيط', re.I)
_VERIFIED_RE = re.compile(
    r'\bverified\s+(?:property|listing|title|documents?|papers|ownership)\b'
    r'|\b(?:title|documents?|ownership|property|listing|papers)\s+(?:is|are|has been|have been)\s+verified\b'
    r'|\b(?:title|documents?|ownership|property|listing|papers|kaghz?a+t|kaghaz)\s+(?:\w+\s+){0,2}'
    r'(?:verified|tasd(?:ee|i)q\s*shuda)\s+ha(?:i|in)\b|تصدیق\s*شدہ|ویریفائ', re.I)
_HEDGE_RE = re.compile(
    r"\b(?:no|not|n't|isn't|doesn't|don't|without|unknown|unspecified|unverified|lacks?|nahi|nahin|if|whether|"
    r"could|would you|do you|ask|confirm with)\b|\?|؟|نہیں|ليس", re.I)
_BUDGET_RE = re.compile(r'\b(?:budget|under|within|up to|upto|max(?:imum)?|below|over|above|range|limit|tak|'
                        r'your (?:price|offer|income|loan))\b|بجٹ|ميزانية', re.I)


def sizes_m2(text: str) -> list[float]:
    from apps.properties.models import Property
    factors = Property._SQM_FACTORS
    out = []
    for pattern in (_SIZE_RE, _M_RE):
        for num, unit in pattern.findall(text):
            u = re.sub(r'[\s.]', '', unit.lower())
            if u.startswith('marla') or unit in ('M', 'مرلہ'):
                f = factors['marla']
            elif u.startswith('kanal') or unit == 'کنال':
                f = factors['kanal']
            elif 'f' in u:
                f = factors['sqft']
            else:
                f = 1.0
            out.append(float(num.replace(',', '')) * float(f))
    return out


def _blocks(reply: str) -> list[str]:
    """Paragraphs, further split at list items that start a new entry."""
    out = []
    for para in re.split(r'\n\s*\n', reply):
        out += [b for b in re.split(r'\n(?=\s*(?:\d{1,2}[.)]|[•▪🏠])\s)', para) if b.strip()]
    return out


_LOOSE_START = re.compile(r'^\s*(?:\d{1,2}[.)]|[•▪🏠])')


# ── Check ─────────────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    violations: list[str] = field(default_factory=list)      # "code: detail" for the model
    cited: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def codes(self) -> list[str]:
        return sorted({v.split(':', 1)[0] for v in self.violations})


def _close(a, b, tolerance) -> bool:
    return b and abs(a - b) <= abs(b) * tolerance


def _attribute(block: str, listings: list[Listing]) -> list[Listing]:
    text = f' {_norm(block)} '
    named = [l for l in listings if any(f' {n} ' in text for n in l.names())]
    if not named:
        named = [l for l in listings if l.location and f' {_norm(l.location)} ' in text]
    return named


def _amenity_terms() -> dict[str, list[str]]:
    from apps.properties.models import Amenity
    terms = cache.get('ai:grounding:amenities')
    if terms is None:
        terms = {}
        for key, labels in Amenity.objects.filter(is_active=True).values_list('key', 'labels'):
            words = {key.replace('_', ' '), str((labels or {}).get('en', '')).lower()}
            terms[key] = sorted(w for w in words if len(w) >= 3)
        cache.set('ai:grounding:amenities', terms, 600)
    return terms


def check(reply: str, evidence: Evidence) -> Verdict:
    verdict = Verdict()
    listings = evidence.listings()
    other = evidence.numbers | {a for a, _ in money(evidence.system)}
    user_money = {a for a, _ in money(evidence.user_text)}
    user_sizes = sizes_m2(evidence.user_text)

    if not listings:
        for line in reply.splitlines():
            amounts = [a for a, _ in money(line)]
            unknown = [a for a in amounts if not any(_close(a, o, PRICE_TOLERANCE) for o in other | user_money)]
            detail = sizes_m2(line) or _BED_RE.search(line)
            if unknown and detail and not _HEDGE_RE.search(line):
                verdict.violations.append('unsourced_listing: no listing was returned by a tool; do not describe one')
                break
        return verdict

    terms = _amenity_terms()
    current: list[Listing] = []
    for block in _blocks(reply):
        named = _attribute(block, listings)
        # an unnamed paragraph right after a listing's paragraph continues it
        if named:
            current = named
        elif not current or _LOOSE_START.match(block):
            current = listings
        pool = named or current
        single = len(pool) == 1
        if named and len(named) <= 5:
            verdict.cited += [l.id for l in named if l.id not in verdict.cited]
        for line in block.splitlines():
            _check_line(line, pool, single, verdict, other, user_money, user_sizes, terms, evidence)
    verdict.violations = list(dict.fromkeys(verdict.violations))
    return verdict


def _check_line(line, pool, single, verdict, other, user_money, user_sizes, terms, evidence):
    budget_line = bool(_BUDGET_RE.search(line))
    hedged = bool(_HEDGE_RE.search(line))
    add = verdict.violations.append

    for amount, cur in money(line):
        match = [l for l in pool if l.price and _close(amount, Decimal(l.price), PRICE_TOLERANCE)]
        if match:
            if cur and not any(not l.currency or l.currency == cur for l in match):
                add(f'currency: {cur} is not the currency of this listing')
            continue
        if any(_close(amount, Decimal(str(n)), PRICE_TOLERANCE) and cur in ('', c)
               for l in pool for n, c in l.amounts):
            continue
        if any(_close(amount, o, PRICE_TOLERANCE) for o in other):
            continue
        if any(_close(amount, t, PRICE_TOLERANCE) for l in pool for t, _ in money(l.text)):
            continue                                   # e.g. a service charge the lister stated
        if (budget_line or not single) and any(_close(amount, u, PRICE_TOLERANCE) for u in user_money):
            continue
        add(f'price: {amount:,.0f} is not the price of any listing provided')

    for m2 in sizes_m2(line):
        if any(_close(m2, s, SIZE_TOLERANCE) for l in pool for s in [l.size_m2, *sizes_m2(l.words)]):
            continue
        if any(_close(m2, u, SIZE_TOLERANCE) for u in user_sizes) and (not single or hedged):
            continue
        add('size: a size in the reply does not match the listing data')

    for code, pattern, attr in (('bedrooms', _BED_RE, 'bedrooms'), ('bathrooms', _BATH_RE, 'bathrooms')):
        for n in pattern.findall(line):
            if hedged or any(getattr(l, attr) == int(n) or n in pattern.findall(l.title) for l in pool):
                continue
            if not single and n in [x for x in pattern.findall(evidence.user_text)]:
                continue
            add(f'{code}: {n} is not in the listing data (say "not specified")')

    if hedged:
        return
    if _INSTALL_RE.search(line) and not any(l.installments for l in pool):
        add('instalments: no listing provided offers instalments')
    if _VERIFIED_RE.search(line) and not any(l.verified for l in pool):
        add('verification: the listing is not verified')
    if single:
        l = pool[0]
        from .compliance_filters import _CITY_RE
        m = _CITY_RE.search(line)
        if m and l.city and m.group(0).isascii() and _norm(m.group(0)) != _norm(l.city) \
                and _norm(m.group(0)) not in _norm(f'{l.title} {l.location} {l.text}'):
            add(f'city: the listing is in {l.city}')
        if l.amenities is not None:
            text, own = f' {_norm(line)} ', _norm(f'{l.title} {l.text}')
            for key, words in terms.items():
                if key in l.amenities:
                    continue
                # a longer amenity the listing does have may contain this word ("private pool")
                if any(f' {w} ' in text and w not in own
                       and not any(w in h for k in l.amenities for h in terms.get(k, ())) for w in words):
                    add(f'amenities: "{words[0]}" is not in the listing data')


# ── Outcome ───────────────────────────────────────────────────────────────────

def correction(verdict: Verdict) -> str:
    return ('[System check] Your last reply was not sent because it contains facts that are not in the tool '
            'results:\n- ' + '\n- '.join(verdict.violations[:8]) +
            '\nRewrite the reply to the user using only facts from the tool results. Say "not specified" for '
            'anything missing. Do not mention this check.')


def fallback(evidence: Evidence) -> str:
    """Facts straight from the sources, plus an offer of a person."""
    from apps.markets.services import format_price
    lines = []
    for n, l in enumerate(evidence.listings()[:5], 1):
        price = format_price(l.price, l.currency, missing='Price on request')
        facts = [price, l.size_label,
                 f'{l.bedrooms} bed' if l.bedrooms else '', f'{l.bathrooms} bath' if l.bathrooms else '']
        place = ', '.join(p for p in (l.location, l.city) if p)
        lines.append(f"{n}. *{l.title}*\n   📍 {place}\n   {' · '.join(f for f in facts if f)}")
    offer = 'Would you like me to connect you with an agent for more details?'
    if not lines:
        return ("I don't have confirmed listing details for that. Tell me the city, budget and type of property "
                'and I will search our listings.\n\n' + offer)
    return 'Here is what our records show:\n\n' + '\n\n'.join(lines) + '\n\n' + offer


def record_violation(verdict: Verdict, evidence: Evidence) -> None:
    """ComplianceViolation row for a reply that had to be replaced (codes only, no reply text)."""
    from ._tools_context import _ctx_org, _ctx_phone
    from .compliance_filters import _persist_violation
    _persist_violation('ungrounded_reply', ', '.join(verdict.codes), _ctx_phone.get(),
                       {'id': (verdict.cited or [''])[0]}, _ctx_org.get())
