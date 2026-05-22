from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MarketConfig:
    country:       str
    currency:      str
    currency_sym:  str
    size_unit:     str    # 'marla' | 'sqft' | 'sqm'
    sqft_per_unit: float  # sqft per primary size unit
    language:      str    # BCP-47 primary language tag
    tax_system:    str    # human-readable label for LLM context
    price_fmt:     str    # how locals express prices
    extra:         dict   = field(default_factory=dict)


MARKET_REGISTRY: dict[str, MarketConfig] = {
    'PK': MarketConfig(
        country='PK', currency='PKR', currency_sym='₨',
        size_unit='marla', sqft_per_unit=272.25,
        language='ur',
        tax_system='Pakistan FBR (Section 7E CVT, CGT, WHT, Stamp Duty)',
        price_fmt='crore / lakh  (1 crore = PKR 10,000,000 ; 1 lakh = PKR 100,000)',
    ),
    'AE': MarketConfig(
        country='AE', currency='AED', currency_sym='د.إ',
        size_unit='sqft', sqft_per_unit=1.0,
        language='en',
        tax_system='UAE — no income tax; 4% DLD Transfer Fee on purchase',
        price_fmt='million / thousand AED',
    ),
    'GB': MarketConfig(
        country='GB', currency='GBP', currency_sym='£',
        size_unit='sqft', sqft_per_unit=1.0,
        language='en',
        tax_system='UK — SDLT on purchase, CGT 18%/24% on gains, annual Council Tax',
        price_fmt='thousands / millions GBP',
    ),
    'US': MarketConfig(
        country='US', currency='USD', currency_sym='$',
        size_unit='sqft', sqft_per_unit=1.0,
        language='en',
        tax_system='US — annual county-level Property Tax (~1-2% of value), federal CGT on sale',
        price_fmt='thousands / millions USD',
    ),
    'CA': MarketConfig(
        country='CA', currency='CAD', currency_sym='$',
        size_unit='sqft', sqft_per_unit=1.0,
        language='en',
        tax_system='Canada — Land Transfer Tax on purchase, combined Federal/Provincial CGT',
        price_fmt='thousands / millions CAD',
    ),
}

_DEFAULT_COUNTRY = 'PK'


def get_market_config(country: str) -> MarketConfig:
    """Return config for country (ISO 3166-1 alpha-2). Falls back to PK."""
    return MARKET_REGISTRY.get(country.upper(), MARKET_REGISTRY[_DEFAULT_COUNTRY])


def register_market(config: MarketConfig) -> None:
    """Programmatically add a new market without touching this file."""
    MARKET_REGISTRY[config.country.upper()] = config


def get_city_map(country: str | None = None) -> dict[str, str]:
    """Return lowercased-key → display-name city lookup.

    Pass an ISO 3166-1 alpha-2 country code to get one market's cities,
    or None to get all markets combined (used by IntentClassifier).
    """
    from apps.markets.pk.cities import CITY_MAP as _PK
    from apps.markets.ae.cities import CITY_MAP as _AE
    from apps.markets.gb.cities import CITY_MAP as _GB

    _all: dict[str, str] = {**_PK, **_AE, **_GB}
    if country is None:
        return _all
    return {'PK': _PK, 'AE': _AE, 'GB': _GB}.get(country.upper(), {})
