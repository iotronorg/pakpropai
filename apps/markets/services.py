"""
Market lookups (T-1050a, ADR-019).

Two kinds of read:

* **Model field defaults** (``default_currency`` etc.) come from the code
  registry for ``settings.DEFAULT_MARKET``. They run whenever a model
  instance is built, including inside migrations, so they must never touch
  the database. Use them as ``default=`` callables instead of literals such
  as ``'PKR'``; a CI test rejects literal market defaults outside the packs.
* **Runtime configuration** (``get_market``) reads the ``Market`` row, cached
  for five minutes and invalidated on save. It falls back to the registry when
  the row is missing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from .registry import MARKET_REGISTRY

logger = logging.getLogger(__name__)

CACHE_TTL = 300
_CACHE_PREFIX = 'market:v1:'

# Area units valid for each registry size unit family.
_AREA_UNITS = {
    'marla': ['marla', 'kanal', 'sqft', 'sqm', 'acre'],
    'sqft': ['sqft', 'sqm', 'acre'],
    'sqm': ['sqm', 'sqft'],
}


@dataclass(frozen=True)
class MarketInfo:
    code: str
    name: str
    status: str
    default_currency: str
    currencies: tuple
    currency_symbol: str
    default_area_unit: str
    area_units: tuple
    default_language: str
    languages: tuple
    timezone: str
    price_format: str
    tax_system: str
    settings: dict
    from_db: bool = False


def default_market_code() -> str:
    code = (getattr(settings, 'DEFAULT_MARKET', 'PK') or 'PK').upper()
    if code not in MARKET_REGISTRY:
        raise LookupError(f'DEFAULT_MARKET={code!r} has no entry in apps/markets/registry.py')
    return code


def registry_info(code: str) -> MarketInfo:
    """MarketInfo straight from the code registry (no DB)."""
    cfg = MARKET_REGISTRY[code.upper()]
    languages = [cfg.language] if cfg.language == 'en' else [cfg.language, 'en']
    return MarketInfo(
        code=cfg.country, name=cfg.name or cfg.country,
        status='active' if cfg.country == 'PK' else 'beta',
        default_currency=cfg.currency, currencies=(cfg.currency,),
        currency_symbol=cfg.currency_sym,
        default_area_unit=cfg.size_unit, area_units=tuple(_AREA_UNITS.get(cfg.size_unit, [cfg.size_unit])),
        default_language=cfg.language, languages=tuple(languages),
        timezone=cfg.timezone, price_format=cfg.price_fmt, tax_system=cfg.tax_system,
        settings=dict(cfg.extra),
    )


def _from_row(row) -> MarketInfo:
    return MarketInfo(
        code=row.code, name=row.name, status=row.status,
        default_currency=row.default_currency, currencies=tuple(row.currencies or ()),
        currency_symbol=row.currency_symbol,
        default_area_unit=row.default_area_unit, area_units=tuple(row.area_units or ()),
        default_language=row.default_language, languages=tuple(row.languages or ()),
        timezone=row.timezone, price_format=row.price_format, tax_system=row.tax_system,
        settings=dict(row.settings or {}), from_db=True,
    )


def get_market(code: str | None = None) -> MarketInfo | None:
    """
    Runtime market configuration. ``None`` code → the default market.
    Returns ``None`` for a code that is neither in the DB nor in the registry.
    """
    code = (code or default_market_code()).upper()
    key = _CACHE_PREFIX + code
    cached = cache.get(key)
    if cached is not None:
        return cached
    from .models import Market
    row = Market.objects.filter(code=code).first()
    if row is not None:
        info = _from_row(row)
    elif code in MARKET_REGISTRY:
        logger.warning('markets: no Market row for %s; using the code registry', code)
        info = registry_info(code)
    else:
        return None
    cache.set(key, info, CACHE_TTL)
    return info


def invalidate(code: str) -> None:
    cache.delete(_CACHE_PREFIX + code.upper())


def market_code_for_country(country: str | None) -> str | None:
    """Code of the ``Market`` row for an ISO country code, or ``None`` (no such market)."""
    code = (country or '').strip().upper()
    if len(code) != 2:
        return None
    from .models import Market
    return code if Market.objects.filter(code=code).exists() else None


# ── Model field defaults (no DB access — safe in migrations) ─────────────────

def default_country() -> str:
    return default_market_code()


def default_currency() -> str:
    return MARKET_REGISTRY[default_market_code()].currency


def default_area_unit() -> str:
    return MARKET_REGISTRY[default_market_code()].size_unit
