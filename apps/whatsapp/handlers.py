"""
Stateful WhatsApp conversation flow handlers.

Each public start_*/handle_* function signature:
    (text, phone, user, ctx) -> (reply: str, new_state: str, ctx_patch: dict)

States used:
    IDLE
    AWAITING_TAX_INPUT
    AWAITING_LOAN_INPUT
    AWAITING_PROPERTY_DETAILS   — search: need more search info
    LISTING_CITY                — listing: awaiting city
    LISTING_LOCATION            — listing: awaiting location/area
    LISTING_DETAILS             — listing: awaiting type + size
    LISTING_PRICE               — listing: awaiting price
    LISTING_CONFIRM             — listing: awaiting yes/no
"""
import re
import logging

logger = logging.getLogger(__name__)


# ── PKR amount parser ─────────────────────────────────────────────────────────

def parse_pkr(text: str):
    t = text.lower().replace(',', '').replace('rs.', '').replace('pkr', '')
    for pattern, multiplier in [
        (r'(\d+(?:\.\d+)?)\s*crore',       10_000_000),
        (r'(\d+(?:\.\d+)?)\s*(?:lakh|lac)', 100_000),
        (r'(\d+(?:\.\d+)?)\s*million',      1_000_000),
        (r'(\d+(?:\.\d+)?)\s*k\b',          1_000),
    ]:
        m = re.search(pattern, t)
        if m:
            return int(float(m.group(1)) * multiplier)
    m = re.search(r'\b(\d{5,})\b', t)
    return int(m.group(1)) if m else None


def _parse_filer(text: str) -> str:
    t = text.lower()
    if 'non' in t:
        return 'non_filer'
    if 'filer' in t:
        return 'filer'
    return 'unknown'


def _parse_years(text: str):
    m = re.search(r'(\d+)\s*(?:year|yr|saal|sal)', text.lower())
    return int(m.group(1)) if m else None


_PROPERTY_TYPE_MAP = {
    'plot': 'plot', 'plots': 'plot',
    'house': 'residential', 'home': 'residential', 'homes': 'residential',
    'flat': 'residential', 'apartment': 'residential', 'apt': 'residential',
    'villa': 'residential', 'bungalow': 'residential', 'portion': 'residential',
    'shop': 'commercial', 'commercial': 'commercial', 'plaza': 'commercial',
    'office': 'commercial', 'warehouse': 'industrial',
}

# furnished_status values match Property.FurnishedStatus
_FURNISHED_KEYWORDS = {
    'furnished':        'furnished',
    'semi furnished':   'semi_furnished',
    'semi-furnished':   'semi_furnished',
    'semifurnished':    'semi_furnished',
    'unfurnished':      'unfurnished',
    'un furnished':     'unfurnished',
}

# construction_status values match Property.ConstructionStatus
_CONSTRUCTION_KEYWORDS = {
    'builder':              'builder',
    'brand new':            'builder',
    'new construction':     'builder',
    'newly built':          'builder',
    'under construction':   'under_construction',
    'under-construction':   'under_construction',
    'ready':                'ready',
    'ready to move':        'ready',
    'ready-to-move':        'ready',
}

_CITIES = [
    'lahore', 'karachi', 'islamabad', 'rawalpindi', 'faisalabad',
    'multan', 'peshawar', 'quetta', 'sialkot', 'gujranwala',
    'dha', 'bahria', 'gulberg', 'defence', 'clifton', 'gulshan',
]


# ── Tax flow ──────────────────────────────────────────────────────────────────

def start_tax_flow(text, phone, user, ctx):
    fmv   = parse_pkr(text)
    filer = _parse_filer(text)
    if fmv and filer != 'unknown':
        return _exec_tax(fmv, filer, user)
    return (
        "Send your property value and filer status.\n\n"
        "Example: *2 crore filer*  or  *50 lakh non-filer*",
        'AWAITING_TAX_INPUT', {}
    )


def handle_tax_input(text, phone, user, ctx):
    if _is_cancel(text):
        return _cancel_reply(), 'IDLE', {}
    fmv = parse_pkr(text)
    if not fmv:
        return (
            "Couldn't read the amount. Try: *2 crore filer*\n"
            "Or type *cancel* to go back.",
            'AWAITING_TAX_INPUT', {}
        )
    filer = _parse_filer(text)
    if filer == 'unknown':
        filer = 'non_filer'
    return _exec_tax(fmv, filer, user)


def _exec_tax(fmv: int, filer: str, user) -> tuple:
    from services.ai_orchestrator import AIOrchestrator
    try:
        r = AIOrchestrator.tax_7e(fmv, filer, user=user)
        if r.get('exempt'):
            reply = (f"Exempt from Section 7E\n"
                     f"Reason: {r.get('exemption_reason', '-')}\n\n"
                     f"{r.get('advice', '')}")
        else:
            tax = r.get('tax_liability_pkr', 0)
            reply = (f"Section 7E Estimate\n\n"
                     f"Property Value: PKR {fmv:,}\n"
                     f"Filer Status: {filer.replace('_', '-')}\n"
                     f"Tax Liability: PKR {tax:,}\n\n"
                     f"{r.get('advice', '')}")
    except Exception:
        logger.exception("Tax AI failed")
        reply = "Tax engine is temporarily unavailable. Please try again."
    _upsert_lead(user, 'tax')
    return reply, 'IDLE', {}


# ── Loan flow ─────────────────────────────────────────────────────────────────

def start_loan_flow(text, phone, user, ctx):
    income, loan, years = _parse_loan(text)
    if income and loan:
        return _exec_loan(income, loan, years or 20, user)
    return (
        "Send your income, loan amount, and tenure.\n\n"
        "Example: *income 1 lakh loan 40 lakh 20 years*",
        'AWAITING_LOAN_INPUT', {}
    )


def handle_loan_input(text, phone, user, ctx):
    if _is_cancel(text):
        return _cancel_reply(), 'IDLE', {}
    income, loan, years = _parse_loan(text)
    if not income or not loan:
        return (
            "Couldn't parse that. Try:\n"
            "*income 1 lakh loan 40 lakh 20 years*\n"
            "Or type *cancel* to go back.",
            'AWAITING_LOAN_INPUT', {}
        )
    return _exec_loan(income, loan, years or 20, user)


def _parse_loan(text: str):
    t = text.lower()
    income = None
    m = re.search(r'(?:income|salary|earn)[:\s]+(.+?)(?=\s+loan|\s+need|\s*$)', t)
    if m:
        income = parse_pkr(m.group(1))

    loan = None
    m = re.search(r'(?:loan|need|amount)[:\s]+(.+?)(?=\s+\d+\s*(?:year|yr|saal)|\s*$)', t)
    if m:
        loan = parse_pkr(m.group(1))

    if not income or not loan:
        vals = []
        for pat, mul in [(r'(\d+(?:\.\d+)?)\s*crore', 10_000_000),
                         (r'(\d+(?:\.\d+)?)\s*(?:lakh|lac)', 100_000),
                         (r'(\d+(?:\.\d+)?)\s*k\b', 1_000)]:
            for mo in re.finditer(pat, t):
                vals.append(int(float(mo.group(1)) * mul))
        if len(vals) >= 2:
            vals.sort()
            if not income: income = vals[0]
            if not loan:   loan   = vals[-1]

    return income, loan, _parse_years(text)


def _exec_loan(income: int, loan: int, years: int, user) -> tuple:
    from services.ai_orchestrator import AIOrchestrator
    try:
        r        = AIOrchestrator.loan_eligibility(income, loan, years, user=user)
        eligible = r.get('eligible', False)
        emi      = r.get('estimated_monthly_emi_pkr', 0)
        max_loan = r.get('max_loan_pkr', 0)
        steps    = r.get('next_steps', [])
        status   = "ELIGIBLE" if eligible else "NOT ELIGIBLE"
        reply    = (f"Loan Eligibility: {status}\n\n"
                    f"Monthly Income: PKR {income:,}\n"
                    f"Loan Requested: PKR {loan:,}\n"
                    f"Est. Monthly EMI: PKR {emi:,}\n"
                    f"Max Loan: PKR {max_loan:,}\n\n"
                    f"{r.get('reason', '')}")
        if steps:
            reply += '\n\nNext steps:\n' + '\n'.join(f"{i+1}. {s}" for i, s in enumerate(steps))
    except Exception:
        logger.exception("Loan AI failed")
        reply = "Loan engine is temporarily unavailable. Please try again."
    _upsert_lead(user, 'loan', budget_min=loan, budget_max=loan)
    return reply, 'IDLE', {}


# ── Property search flow ──────────────────────────────────────────────────────

def start_property_search(text, phone, user, ctx):
    city, size, budget, ptype, furnished, construction = _parse_property_query(text)
    if city or budget or size or ptype:
        return _exec_search(city, size, budget, ptype, furnished, construction, user, offset=0)
    return (
        "Tell me what you're looking for.\n\n"
        "Examples:\n"
        "• *5 marla plot DHA Lahore under 1 crore*\n"
        "• *furnished flat Gulberg under 50 lakh*\n"
        "• *builder house 10 marla Bahria Town*\n\n"
        "Or send a voice message describing what you need.",
        'AWAITING_PROPERTY_DETAILS', {}
    )


def handle_property_details(text, phone, user, ctx):
    if _is_cancel(text):
        return _cancel_reply(), 'IDLE', {}
    city, size, budget, ptype, furnished, construction = _parse_property_query(text)
    return _exec_search(city, size, budget, ptype, furnished, construction, user, offset=0)


def handle_more_results(phone, user, ctx) -> tuple:
    """Send the next page of a previous search stored in session."""
    results_raw = ctx.get('search_results', [])
    offset      = ctx.get('search_offset', 0)

    if not results_raw:
        return "No previous search found. Send your property query to start.", 'IDLE', {}

    from apps.properties.scrapers.base import PropertyResult
    from apps.properties.search import PropertySearchService, PAGE_SIZE
    results = [PropertyResult.from_dict(d) for d in results_raw]
    total   = len(results)

    if offset >= total:
        return "You've seen all listings. Start a new search anytime.", 'IDLE', {}

    reply       = PropertySearchService.format_page(results, offset, total)
    new_offset  = offset + PAGE_SIZE
    new_state   = 'IDLE' if new_offset >= total else 'SEARCH_PAGINATING'
    ctx_patch   = {'search_offset': new_offset}
    return reply, new_state, ctx_patch


def _parse_property_query(text: str):
    t    = text.lower()
    city = next((c for c in _CITIES if c in t), None)

    size = None
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:marla|mrla)', t)
    if m:
        size = float(m.group(1))
    else:
        m = re.search(r'(\d+(?:\.\d+)?)\s*kanal', t)
        if m:
            size = float(m.group(1)) * 20

    budget = parse_pkr(text)

    ptype = None
    for kw, canonical in _PROPERTY_TYPE_MAP.items():
        if kw in t:
            ptype = canonical
            break

    # Multi-word keywords checked longest-first to avoid partial matches
    furnished = None
    for kw in sorted(_FURNISHED_KEYWORDS, key=len, reverse=True):
        if kw in t:
            furnished = _FURNISHED_KEYWORDS[kw]
            break

    construction = None
    for kw in sorted(_CONSTRUCTION_KEYWORDS, key=len, reverse=True):
        if kw in t:
            construction = _CONSTRUCTION_KEYWORDS[kw]
            break

    return city, size, budget, ptype, furnished, construction


def _exec_search(city, size, budget, ptype, furnished, construction, user, offset: int = 0) -> tuple:
    from apps.properties.search import PropertySearchService, PAGE_SIZE

    results = PropertySearchService.search(
        city=city or '', location='',
        area_marla=size, max_price=budget,
        property_type=ptype or '',
        furnished_status=furnished or '',
        construction_status=construction or '',
    )
    total = len(results)

    if not total:
        where = f" in {city.title()}" if city else ""
        reply = (f"No listings found{where} matching your criteria yet.\n\n"
                 "I've noted your search. We add new properties daily — "
                 "try again tomorrow or broaden your criteria.")
        _upsert_lead(user, 'buy', city_interest=city or '', budget_max=budget)
        return reply, 'IDLE', {}

    reply      = PropertySearchService.format_page(results, 0, total)
    new_offset = PAGE_SIZE
    new_state  = 'SEARCH_PAGINATING' if new_offset < total else 'IDLE'

    _upsert_lead(user, 'buy', city_interest=city or '', budget_max=budget)

    ctx_patch = {
        'search_results': [r.to_dict() for r in results],
        'search_offset':  new_offset,
    }
    return reply, new_state, ctx_patch


# ── Property listing flow (5 steps) ──────────────────────────────────────────

def start_listing_flow(_text, _phone, _user, _ctx):
    return (
        "Let's list your property.\n\n"
        "Which *city* is the property in?\n"
        "Example: *Lahore*, *Karachi*, *Islamabad*",
        'LISTING_CITY', {}
    )


def handle_listing_city(text, phone, user, ctx):
    if _is_cancel(text): return _cancel_reply(), 'IDLE', {}
    city = text.strip().title()
    return (
        f"Got it — *{city}*.\n\n"
        "What's the exact *location or area*?\n"
        "Example: *DHA Phase 6*, *Gulberg 3*, *Bahria Town Block D*",
        'LISTING_LOCATION', {'listing': {'city': city}}
    )


def handle_listing_location(text, phone, user, ctx):
    if _is_cancel(text): return _cancel_reply(), 'IDLE', {}
    location = text.strip().title()
    listing  = {**ctx.get('listing', {}), 'location': location}
    return (
        f"Location: *{location}*\n\n"
        "Property *type and size*? You can also include furnished status and condition.\n\n"
        "Examples:\n"
        "• *5 marla plot*\n"
        "• *10 marla house*\n"
        "• *2 bedroom furnished flat*\n"
        "• *builder house 10 marla*\n"
        "• *unfurnished apartment 1 kanal*",
        'LISTING_DETAILS', {'listing': listing}
    )


def handle_listing_details(text, phone, user, ctx):
    if _is_cancel(text): return _cancel_reply(), 'IDLE', {}

    t     = text.lower()
    size  = _parse_size(text)
    ptype = next((v for k, v in _PROPERTY_TYPE_MAP.items() if k in t), 'residential')

    furnished = None
    for kw in sorted(_FURNISHED_KEYWORDS, key=len, reverse=True):
        if kw in t:
            furnished = _FURNISHED_KEYWORDS[kw]
            break

    construction = None
    for kw in sorted(_CONSTRUCTION_KEYWORDS, key=len, reverse=True):
        if kw in t:
            construction = _CONSTRUCTION_KEYWORDS[kw]
            break

    listing  = {
        **ctx.get('listing', {}),
        'area_marla':          size,
        'property_type':       ptype,
        'furnished_status':    furnished,
        'construction_status': construction,
    }
    size_str    = f"{size}M" if size else "size unknown"
    extras      = []
    if furnished:    extras.append(furnished.replace('_', '-'))
    if construction: extras.append(construction.replace('_', ' '))
    extras_str  = f" | {', '.join(extras)}" if extras else ""
    return (
        f"Type: *{ptype}* | Size: *{size_str}*{extras_str}\n\n"
        "What's the *asking price*?\n"
        "Example: *1.5 crore*, *50 lakh*, *PKR 12000000*",
        'LISTING_PRICE', {'listing': listing}
    )


def handle_listing_price(text, phone, user, ctx):
    if _is_cancel(text): return _cancel_reply(), 'IDLE', {}
    price = parse_pkr(text)
    if not price:
        return (
            "Couldn't read the price. Try: *1.5 crore* or *50 lakh*\n"
            "Or type *cancel* to go back.",
            'LISTING_PRICE', {}
        )
    listing      = {**ctx.get('listing', {}), 'price_pkr': price}
    city         = listing.get('city', '')
    location     = listing.get('location', '')
    area         = listing.get('area_marla', '')
    ptype        = listing.get('property_type', 'property')
    furnished    = listing.get('furnished_status', '')
    construction = listing.get('construction_status', '')

    area_str  = f"{area}M | " if area else ""
    extras    = [s.replace('_', '-') for s in [furnished, construction] if s]
    extra_str = f" | {', '.join(extras)}" if extras else ""

    return (
        f"Please confirm your listing:\n\n"
        f"*{ptype.title()}* — {location}, {city}\n"
        f"{area_str}PKR {price:,}{extra_str}\n\n"
        "Reply *yes* to publish or *cancel* to abort.",
        'LISTING_CONFIRM', {'listing': listing}
    )


def handle_listing_confirm(text, phone, user, ctx):
    if _is_cancel(text) or text.lower().strip() == 'no':
        return "Listing cancelled. No property was saved.", 'IDLE', {}

    if text.lower().strip() not in ('yes', 'y', 'confirm', 'ok', 'haan'):
        return (
            "Reply *yes* to publish or *cancel* to abort.",
            'LISTING_CONFIRM', {}
        )

    listing = ctx.get('listing', {})
    return _create_property(listing, user)


def _parse_size(text: str):
    t = text.lower()
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:marla|mrla)', t)
    if m: return float(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*kanal', t)
    if m: return float(m.group(1)) * 20
    return None


def _create_property(listing: dict, user) -> tuple:
    from apps.properties.models import Property
    try:
        city     = listing.get('city', '')
        location = listing.get('location', '')
        area     = listing.get('area_marla')
        price    = listing.get('price_pkr')
        ptype    = listing.get('property_type', 'residential')
        area_str = f"{area}M " if area else ""
        title    = f"{area_str}{ptype.title()} — {location}, {city}"

        prop = Property.objects.create(
            owner                = user,
            title                = title,
            city                 = city,
            location             = location,
            area_marla           = area,
            price_pkr            = price,
            property_type        = ptype,
            furnished_status     = listing.get('furnished_status'),
            construction_status  = listing.get('construction_status'),
            legal_status         = Property.LegalStatus.UNVERIFIED,
        )

        # Queue AI scoring asynchronously
        try:
            from apps.properties.tasks import score_property_task
            score_property_task.delay(str(prop.id))
        except Exception:
            pass

        price_str = f"PKR {price:,}" if price else "price TBD"
        reply = (f"Listing published!\n\n"
                 f"ID: {str(prop.id)[:8].upper()}\n"
                 f"{title}\n"
                 f"{price_str}\n\n"
                 f"AI scoring is running in the background. "
                 f"Your listing is now visible to buyers searching this area.")
    except Exception:
        logger.exception("Property creation failed")
        reply = "Failed to save listing. Please try again."

    return reply, 'IDLE', {}


# ── Lead capture (silent) ─────────────────────────────────────────────────────

def _upsert_lead(user, intent: str, city_interest: str = '',
                 budget_min: int = None, budget_max: int = None):
    from apps.leads.models import Lead
    try:
        lead, created = Lead.objects.get_or_create(
            user=user,
            defaults={
                'intent':        intent,
                'city_interest': city_interest,
                'budget_min':    budget_min,
                'budget_max':    budget_max,
                'score':         10,
            }
        )
        if not created:
            if intent:        lead.intent        = intent
            if city_interest: lead.city_interest  = city_interest
            if budget_min:    lead.budget_min     = budget_min
            if budget_max:    lead.budget_max     = budget_max
            lead.score = min(lead.score + 5, 100)
            lead.save(update_fields=[
                'intent', 'city_interest', 'budget_min',
                'budget_max', 'score', 'last_scored_at',
            ])
    except Exception:
        logger.exception("Lead upsert failed")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_cancel(text: str) -> bool:
    return text.lower().strip() in ('cancel', 'menu', 'main menu', 'back', 'reset', '/start')


def _cancel_reply() -> str:
    return (
        "Cancelled. What else can I help you with?\n\n"
        "• Property search\n• List a property\n• Tax (7E)\n• Loan\n• Scam check"
    )
