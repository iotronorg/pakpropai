"""
Investment calculator (T-4010, FR-INV-001…004, ADR-065).

Deterministic: the same inputs give the same numbers, and every figure carries
its formula and the inputs it used. Every assumption carries its source:

- ``user``              the caller gave it;
- ``listing``           read from the listing (price, currency, country);
- ``platform_median``   median monthly asking rent of comparable published rent
                        listings, with their number and date (never below ``MIN_COMPARABLES``);
- ``market_calculator`` the market's tax and fee calculator (``apps.markets.calculators``);
- ``platform_default``  a stated platform assumption, shown so the user can change it.

Nothing here is a valuation, a rent estimate or financial advice: rents are
asking prices, and appreciation is only ever the user's own scenario.
Money is in major units of one currency; figures are rounded to whole units.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from statistics import median

from django.conf import settings
from django.utils import timezone

MIN_COMPARABLES = getattr(settings, 'INVESTMENT_MIN_COMPARABLES', 5)
DISCLAIMER = ('General information, not financial advice. Rents are asking prices of listings on this '
              'platform, not achieved rents; taxes and fees are estimates. Check figures with a licensed adviser.')
DEFAULTS = {                          # platform assumptions, always labelled as such
    'vacancy_pct': Decimal('8.33'),            # about one month a year empty
    'maintenance_pct': Decimal('5'),           # of collected rent
    'management_pct': Decimal('0'),
    'holding_years': 5,
}
# What the market calculators are asked for an investor buying (one more property).
TAX_ARGS = {'PK': {'filer_status': 'filer', 'properties_count': 2, 'is_self_occupied': False},
            'AE': {'is_purchase': True}, 'GB': {'buyer_type': 'additional'}, 'US': {}}
LIMITS = {'price': (1, 10 ** 13), 'monthly_rent': (0, 10 ** 11), 'pct': (0, 100), 'rate': (0, 50),
          'tenure_years': (1, 40), 'holding_years': (1, 40), 'other': (0, 10 ** 12)}


class InvestmentError(Exception):
    def __init__(self, message: str, field: str = 'detail'):
        super().__init__(message)
        self.field = field


@dataclass
class Assumption:
    name: str
    value: object
    source: str
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        v = self.value
        return {'name': self.name, 'value': float(v) if isinstance(v, Decimal) else v, 'source': self.source,
                **({'detail': self.detail} if self.detail else {})}


def _d(value, name, kind='other') -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise InvestmentError('A number.', name) from None
    lo, hi = LIMITS[kind]
    if not d.is_finite() or d < lo or d > hi:
        raise InvestmentError(f'A number from {lo} to {hi}.', name)
    return d


def _whole(d: Decimal) -> int:
    return int(d.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _pct(d: Decimal) -> float:
    return float(d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


# ── Rents of comparable listings ──────────────────────────────────────────────

def comparable_rent(*, country, city, currency, category=None, subtype=None, bedrooms=None, size_m2=None,
                    exclude=None) -> dict | None:
    """Median monthly asking rent of comparable published rent listings; ``None`` below ``MIN_COMPARABLES``."""
    from .models import Property
    P = Property
    qs = (Property.objects.visible_to(None)
          .filter(purpose=P.Purpose.RENT_LONG, country=country, city__iexact=city, currency=currency,
                  price_on_request=False, price__gt=0,
                  price_type__in=(P.PriceType.PER_MONTH, P.PriceType.PER_YEAR)))
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    if category:
        qs = qs.filter(category=category)
    if subtype:
        qs = qs.filter(subtype=subtype)
    if bedrooms is not None:
        qs = qs.filter(bedrooms=bedrooms)
    band = None
    if size_m2:
        lo, hi = Decimal(size_m2) * Decimal('0.75'), Decimal(size_m2) * Decimal('1.25')
        qs, band = qs.filter(size_m2__gte=lo, size_m2__lte=hi), [_whole(lo), _whole(hi)]
    rows = list(qs.values_list('price', 'price_type')[:500])
    monthly = sorted(Decimal(p) / (12 if t == P.PriceType.PER_YEAR else 1) for p, t in rows)
    if len(monthly) < MIN_COMPARABLES:
        return None
    q = len(monthly) // 4
    return {'median': _whole(Decimal(median(monthly))), 'n': len(monthly), 'p25': _whole(monthly[q]),
            'p75': _whole(monthly[-q - 1]), 'as_of': timezone.now().date().isoformat(),
            'filters': {'city': city, 'category': category, 'subtype': subtype, 'bedrooms': bedrooms,
                        'size_m2_band': band, 'currency': currency}}


# ── The calculation ───────────────────────────────────────────────────────────

def _mortgage_payment(principal: Decimal, rate_pct: Decimal, years: int) -> Decimal:
    n = years * 12
    if principal <= 0:
        return Decimal(0)
    r = rate_pct / Decimal(1200)
    if r == 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def _taxes(country: str, price: Decimal) -> dict:
    from apps.markets.engine import FinancialEngine
    res = FinancialEngine.calculate_tax(country, _whole(price), **TAX_ARGS.get(country, {}))
    if not res.supported:
        return {'supported': False}
    return {'supported': True, 'purchase': (res.transfer_tax or 0) + (res.stamp_duty or 0),
            'annual': res.annual_tax or 0, 'notes': list(res.notes or []), 'advice': res.advice}


def calculate(data: dict, *, listing=None) -> dict:
    """``data``: the caller's inputs (see ``apps.properties.investment_views``); ``listing`` fills the rest."""
    a: list[Assumption] = []

    def take(name, kind, default=None, source='platform_default'):
        if data.get(name) not in (None, ''):
            v = _d(data[name], name, kind)
            a.append(Assumption(name, v, 'user'))
            return v
        if default is not None:
            a.append(Assumption(name, Decimal(default), source))
            return Decimal(default)
        return None

    # The property
    if listing is not None:
        if listing.price_on_request or not listing.price or listing.price_type != listing.PriceType.TOTAL \
                or listing.purpose != listing.Purpose.SALE:
            raise InvestmentError('This listing has no sale price to calculate with.', 'listing')
    price = take('price', 'price', listing.price if listing is not None else None, 'listing')
    if price is None:
        raise InvestmentError('A purchase price, or a listing.', 'price')
    country = (data.get('country') or (listing.country if listing is not None else '') or '').upper()[:2]
    currency = (data.get('currency') or (listing.currency if listing is not None else '') or '').upper()[:3]
    if not country or not currency:
        raise InvestmentError('The country and currency, or a listing.', 'country')
    a.append(Assumption('currency', currency, 'user' if data.get('currency') else 'listing'))

    # Rent
    rent_source = None
    rent = take('monthly_rent', 'monthly_rent')
    if rent is None and listing is not None:
        rent_source = comparable_rent(country=country, city=listing.city, currency=currency,
                                      category=listing.category, subtype=listing.subtype,
                                      bedrooms=listing.bedrooms, size_m2=listing.size_m2, exclude=listing.pk)
        if rent_source is not None:
            rent = Decimal(rent_source['median'])
            a.append(Assumption('monthly_rent', rent, 'platform_median', rent_source))
    if rent is None:
        raise InvestmentError(f'A monthly rent: fewer than {MIN_COMPARABLES} comparable rent listings to '
                              'take a median from.', 'monthly_rent')

    vacancy = take('vacancy_pct', 'pct', DEFAULTS['vacancy_pct'])
    maintenance = take('maintenance_pct', 'pct', DEFAULTS['maintenance_pct'])
    management = take('management_pct', 'pct', DEFAULTS['management_pct'])
    other_annual = take('other_annual_costs', 'other', 0, 'platform_default')
    holding = int(take('holding_years', 'holding_years', DEFAULTS['holding_years']))

    # Market taxes and fees (estimates), unless the user gives their own
    tax = _taxes(country, price)
    if data.get('purchase_costs') not in (None, ''):
        purchase_costs = take('purchase_costs', 'other')
    elif tax['supported']:
        purchase_costs = Decimal(tax['purchase'])
        a.append(Assumption('purchase_costs', purchase_costs, 'market_calculator', {'notes': tax['notes']}))
    else:
        purchase_costs = Decimal(0)
        a.append(Assumption('purchase_costs', purchase_costs, 'platform_default',
                            {'note': f'No fee calculator for {country}; add your own.'}))
    if data.get('annual_property_tax') not in (None, ''):
        annual_tax = take('annual_property_tax', 'other')
    elif tax['supported']:
        annual_tax = Decimal(tax['annual'])
        a.append(Assumption('annual_property_tax', annual_tax, 'market_calculator'))
    else:
        annual_tax = Decimal(0)
        a.append(Assumption('annual_property_tax', annual_tax, 'platform_default'))

    # Financing: a cash purchase unless the user gives a loan
    down_pct = take('down_payment_pct', 'pct')
    rate = take('interest_rate_pct', 'rate')
    tenure = take('tenure_years', 'tenure_years')
    financed = down_pct is not None and down_pct < 100
    if financed and (rate is None or tenure is None):
        raise InvestmentError('With a down payment below 100%, give interest_rate_pct and tenure_years.',
                              'interest_rate_pct')
    down = price * (down_pct / 100) if financed else price
    loan = price - down
    payment = _mortgage_payment(loan, rate, int(tenure)) if financed else Decimal(0)
    debt_service = payment * 12

    # Figures
    gross = rent * 12
    collected = gross * (1 - vacancy / 100)
    operating = collected * (maintenance + management) / 100 + annual_tax + other_annual
    noi = collected - operating
    cash_in = down + purchase_costs
    cash_flow = noi - debt_service

    def fig(name, value, formula, unit='money'):
        return {'name': name, 'value': _whole(value) if unit == 'money' else _pct(value), 'unit': unit,
                'formula': formula}

    figures = [
        fig('gross_annual_rent', gross, 'monthly_rent × 12'),
        fig('collected_rent', collected, 'gross_annual_rent × (1 − vacancy_pct)'),
        fig('operating_costs', operating, 'collected_rent × (maintenance_pct + management_pct) '
                                          '+ annual_property_tax + other_annual_costs'),
        fig('net_operating_income', noi, 'collected_rent − operating_costs'),
        fig('gross_yield_pct', gross / price * 100, 'gross_annual_rent ÷ price × 100', 'pct'),
        fig('net_yield_pct', noi / price * 100, 'net_operating_income ÷ price × 100', 'pct'),
        fig('cash_invested', cash_in, 'down payment + purchase_costs'),
        fig('loan_amount', loan, 'price − down payment'),
        fig('monthly_loan_payment', payment, 'standard repayment loan: loan × r(1+r)^n ÷ ((1+r)^n − 1), '
                                             'r = interest_rate_pct ÷ 1200, n = tenure_years × 12'),
        fig('annual_cash_flow', cash_flow, 'net_operating_income − monthly_loan_payment × 12'),
        fig('cash_on_cash_pct', cash_flow / cash_in * 100 if cash_in else Decimal(0),
            'annual_cash_flow ÷ cash_invested × 100', 'pct'),
    ]

    # Scenarios: only the user's own appreciation rates (none assumed by the platform)
    scenarios = []
    rates = data.get('appreciation_pct') or []
    if not isinstance(rates, list) or len(rates) > 3:
        raise InvestmentError('A list of at most 3 yearly rates.', 'appreciation_pct')
    for raw in rates:
        g = _d(raw, 'appreciation_pct', 'rate')
        value = price * (1 + g / 100) ** holding
        balance = _balance(loan, rate, int(tenure), holding) if financed else Decimal(0)
        equity_gain = value - price
        total = cash_flow * holding + equity_gain
        scenarios.append({'appreciation_pct': float(g), 'holding_years': holding, 'value_at_end': _whole(value),
                          'loan_balance_at_end': _whole(balance), 'total_return': _whole(total),
                          'roi_pct': _pct(total / cash_in * 100) if cash_in else 0.0,
                          'formula': '(annual_cash_flow × holding_years + value_at_end − price) '
                                     '÷ cash_invested × 100; selling costs not included',
                          'source': 'user'})

    notes = []
    if tax['supported'] and tax.get('advice'):
        notes.append(tax['advice'])
    if country == 'PK':
        notes.append('Advance tax on purchase (section 236K) is not included; it depends on filer status.')
    return {'currency': currency, 'country': country, 'price': _whole(price),
            'listing_id': str(listing.pk) if listing is not None else None,
            'figures': figures, 'scenarios': scenarios, 'assumptions': [x.as_dict() for x in a],
            'notes': notes, 'disclaimer': DISCLAIMER}


def _balance(loan: Decimal, rate_pct: Decimal, years: int, after_years: int) -> Decimal:
    if loan <= 0:
        return Decimal(0)
    k = min(after_years, years) * 12
    r = rate_pct / Decimal(1200)
    if r == 0:
        return loan * (1 - Decimal(k) / (years * 12))
    p = _mortgage_payment(loan, rate_pct, years)
    return max(Decimal(0), loan * (1 + r) ** k - p * ((1 + r) ** k - 1) / r)


def numbers(result: dict) -> set[str]:
    """Every number the result shows, as the commentary may quote it (for the grounding check)."""
    out = {str(result['price'])}
    for f in result['figures']:
        out.add(str(f['value']))
    for s in result['scenarios']:
        out.update(str(v) for k, v in s.items() if isinstance(v, (int, float)))
    for x in result['assumptions']:
        if isinstance(x['value'], (int, float)):
            out.add(str(x['value']))
        if x['source'] == 'platform_median':
            out.update(str(x['detail'][k]) for k in ('n', 'p25', 'p75', 'median'))
    return out

