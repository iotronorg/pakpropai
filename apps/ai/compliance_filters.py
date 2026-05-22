"""
Outbound Compliance & Hallucination Filter — RealTron AI (global multi-market edition).

Pipeline position: LLM reply → ComplianceEngine.check_output() → WhatsApp API send.

Checks:
    1. Price variance      — AI claims a price that deviates >PRICE_VARIANCE_THRESHOLD
                             from the property's real-time DB record.
    2. Currency mismatch   — AI claims a price in a different currency than DB.
    3. False installments  — AI offers a payment plan when installment_available=False.
    4. City mismatch       — AI mentions a city that doesn't match DB property.city.

Property context resolved from:
    a. Explicit property_id argument (highest priority).
    b. Redis key set by ComplianceEngine.set_active_property().
    c. None → compliance check skipped (cannot verify without ground truth).

Supported currencies: PKR, AED, USD, GBP, EUR, SAR, INR, MYR, SGD, CAD, AUD.
Supported city scripts: Latin, Arabic, Urdu, Hindi (Devanagari), Russian, Chinese.

Redis cache TTL: PROPERTY_CACHE_TTL seconds (default 60 s).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PRICE_VARIANCE_THRESHOLD = 0.10   # 10 %
PROPERTY_CACHE_TTL       = 60     # seconds
_CACHE_PREFIX            = 'compliance:prop:'


# ──────────────────────────────────────────────────────────────────────────────
# Price extraction — multi-currency, module-level compiled
# ──────────────────────────────────────────────────────────────────────────────

# South Asian units (PKR / INR)
_CRORE_RE   = re.compile(r'(\d+(?:\.\d+)?)\s*crore',   re.I)
_LAKH_RE    = re.compile(r'(\d+(?:\.\d+)?)\s*lakh',    re.I)

# International units
_MILLION_RE = re.compile(r'(\d+(?:\.\d+)?)\s*million', re.I)
_BILLION_RE = re.compile(r'(\d+(?:\.\d+)?)\s*billion', re.I)
_THOUSAND_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*[Kk]\b(?!\s*(?:arachi|uala|iev|m))',  # avoid K in city names
)

# Raw currency notations
_RAW_PKR_RE = re.compile(
    r'(?:PKR|Rs\.?|₨)\s*(\d{1,3}(?:[,.\s]\d{2,3})*)',
    re.I,
)
_RAW_AED_RE = re.compile(
    r'(?:AED|د\.إ|درهم)\s*(\d[\d,.\s]*)',
    re.I | re.UNICODE,
)
_RAW_USD_RE = re.compile(
    r'(?:USD|\$)\s*(\d[\d,.\s]*)',
    re.I,
)
_RAW_GBP_RE = re.compile(
    r'(?:GBP|£)\s*(\d[\d,.\s]*)',
    re.I,
)
_RAW_EUR_RE = re.compile(
    r'(?:EUR|€)\s*(\d[\d,.\s]*)',
    re.I,
)
_RAW_SAR_RE = re.compile(
    r'(?:SAR|ريال\s+سعودي|ر\.س\.?)\s*(\d[\d,.\s]*)',
    re.I | re.UNICODE,
)
_RAW_INR_RE = re.compile(
    r'(?:INR|₹)\s*(\d[\d,.\s]*)',
    re.I,
)

# Currency-code words appearing after a number: "500,000 AED" / "250K USD"
_SUFFIX_CURRENCY_RE = re.compile(
    r'(\d[\d,.\s]*)\s*'
    r'(AED|USD|GBP|EUR|SAR|INR|PKR|MYR|SGD|CAD|AUD)\b',
    re.I,
)

# Map from pattern / keyword to ISO currency code
_WORD_TO_CURRENCY: dict[str, str] = {
    'dirham': 'AED', 'dirhams': 'AED', 'درهم': 'AED',
    'dollar': 'USD', 'dollars': 'USD',
    'pound':  'GBP', 'pounds':  'GBP',
    'euro':   'EUR', 'euros':   'EUR',
    'riyal':  'SAR', 'riyals':  'SAR', 'ريال': 'SAR',
    'rupee':  'INR', 'rupees':  'INR',  # generic; PKR overrides below if PKR signals present
}


# ──────────────────────────────────────────────────────────────────────────────
# Installment detection — multilingual
# ──────────────────────────────────────────────────────────────────────────────

_INSTALLMENT_RE = re.compile(
    r'\b('
    # English / Pakistani
    r'installment|installments|monthly\s+payment|easy\s+installment|'
    r'emi|down\s+payment\s+plan|payment\s+plan|qist|'
    r'installment\s+plan|flexible\s+payments'
    r')\b'
    # Arabic
    r'|(?:بالتقسيط|أقساط\s+شهرية|نظام\s+الأقساط|دفعات\s+شهرية)'
    # French
    r'|\b(?:paiement\s+échelonné|versements?\s+mensuels?|facilités?\s+de\s+paiement)\b'
    # Spanish
    r'|\b(?:pago\s+a\s+plazos|cuotas?\s+mensuales?|financiamiento)\b'
    # German
    r'|\b(?:Ratenzahlung|monatliche\s+Zahlung|Teilzahlung)\b'
    # Turkish
    r'|\b(?:taksit|taksitli\s+ödeme|aylık\s+ödeme)\b'
    # Russian
    r'|\b(?:рассрочка|ежемесячный\s+платёж|оплата\s+в\s+рассрочку)\b'
    # Chinese
    r'|(?:分期付款|月供|按揭)',
    re.I | re.UNICODE,
)

# ──────────────────────────────────────────────────────────────────────────────
# City detection — global Latin + Arabic + Urdu + Hindi + Russian + Chinese
# ──────────────────────────────────────────────────────────────────────────────

_CITY_RE = re.compile(
    r'\b(?:'
    # Pakistan — Latin
    r'lahore|karachi|islamabad|rawalpindi|faisalabad|multan|peshawar|quetta|'
    r'sialkot|gujranwala|hyderabad|bahawalpur|sargodha|sukkur|abbottabad|'
    # UAE — Latin
    r'dubai|abu\s+dhabi|sharjah|ajman|fujairah|ras\s+al\s+khaimah|'
    # Saudi — Latin
    r'riyadh|jeddah|mecca|medina|dammam|'
    # UK / Europe
    r'london|manchester|birmingham|leeds|paris|berlin|munich|amsterdam|'
    # North America
    r'new\s+york|los\s+angeles|toronto|vancouver|montreal|'
    # Asia-Pacific
    r'singapore|kuala\s+lumpur|sydney|melbourne|auckland|'
    r'mumbai|delhi|bangalore|chennai|hyderabad|'
    # Turkey
    r'istanbul|ankara|izmir'
    r')\b'
    # Arabic script cities (UAE, Saudi, Pakistan)
    r'|(?:دبي|أبوظبي|أبو\s*ظبي|الشارقة|عجمان|'
    r'الرياض|جدة|مكة|المدينة\s*المنورة|الدمام|'
    r'لاهور|كراتشي|إسلام\s*آباد)'
    # Urdu script
    r'|(?:لاہور|کراچی|اسلام\s*آباد|راولپنڈی|فیصل\s*آباد|ملتان|پشاور)'
    # Hindi / Devanagari
    r'|(?:मुंबई|दिल्ली|बेंगलुरु|बैंगलोर|चेन्नई|हैदराबाद|'
    r'लाहौर|कराची|इस्लामाबाद)'
    # Russian
    r'|(?:Москва|Санкт-Петербург|Дубай|Лондон)'
    # Chinese Simplified
    r'|(?:上海|北京|深圳|广州|迪拜|伦敦)',
    re.I | re.UNICODE,
)


# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PropertyClaim:
    """Structured entities extracted from an LLM reply."""
    price_amount:         Optional[int] = None
    price_currency:       Optional[str] = None  # ISO-4217 code or None
    city:                 Optional[str] = None
    installments_offered: bool          = False

    @property
    def price_pkr(self) -> Optional[int]:
        """Backward-compatibility alias for price_amount."""
        return self.price_amount


@dataclass
class ComplianceResult:
    safe:          bool
    action_taken:  str            = 'none'  # none | blocked_price | blocked_installment | blocked_location | blocked_currency_mismatch
    variance_pct:  Optional[float] = None
    db_price:      Optional[int]   = None
    claimed_price: Optional[int]   = None
    details:       str             = ''
    human_reply:   str             = ''


# ──────────────────────────────────────────────────────────────────────────────
# ComplianceEngine
# ──────────────────────────────────────────────────────────────────────────────

class ComplianceEngine:
    """
    Stateless, thread-safe post-LLM compliance gate.

    Usage:
        result = ComplianceEngine.check_output(reply, org=org, phone=phone)
        if not result.safe:
            send_whatsapp(phone, result.human_reply)
        else:
            send_whatsapp(phone, reply)
    """

    @classmethod
    def check_output(
        cls,
        reply:       str,
        org=None,
        phone:       Optional[str] = None,
        property_id: Optional[str] = None,
    ) -> ComplianceResult:
        if not reply or not reply.strip():
            return ComplianceResult(safe=True)

        claims = _extract_claims(reply)

        if (claims.price_amount is None
                and not claims.installments_offered
                and claims.city is None):
            return ComplianceResult(safe=True)

        prop = cls._resolve_property(org, phone, property_id)
        if prop is None:
            logger.debug(
                "ComplianceEngine: no property context for phone=%s — skipping", phone
            )
            return ComplianceResult(safe=True)

        # Check 1: currency mismatch
        if claims.price_amount is not None and claims.price_currency and prop.get('currency'):
            if claims.price_currency.upper() != prop['currency'].upper():
                return _block_currency_mismatch(
                    claims.price_currency, prop['currency'], prop, phone, org
                )

        # Check 2: price variance (same currency path)
        if claims.price_amount is not None and prop.get('price') is not None:
            result = _check_price(
                claimed=claims.price_amount,
                db_price=int(prop['price']),
                prop=prop,
                phone=phone,
                org=org,
            )
            if not result.safe:
                return result

        # Check 3: false installment promise
        if claims.installments_offered and not prop.get('installment_available', False):
            return _block_installment(prop, phone, org)

        # Check 4: city / location mismatch
        if claims.city and prop.get('city'):
            if claims.city.lower() != prop['city'].lower():
                return _block_location(claims.city, prop['city'], prop, phone, org)

        return ComplianceResult(safe=True)

    @classmethod
    def _resolve_property(
        cls,
        org,
        phone:       Optional[str],
        property_id: Optional[str],
    ) -> Optional[dict]:
        if property_id:
            return _load_property_by_id(property_id)
        if phone and org:
            org_id    = str(getattr(org, 'id', ''))
            cache_key = f"{_CACHE_PREFIX}{org_id}:{phone.lstrip('+')}"
            return _redis_get(cache_key)
        return None

    @staticmethod
    def set_active_property(phone: str, org_id: str, property_data: dict) -> None:
        """
        Register the property currently being discussed for a phone+org pair.
        Call from the AI service layer when a property enters conversation context.
        property_data must contain at minimum: {price, currency, city, installment_available}.
        """
        try:
            cache_key = f"{_CACHE_PREFIX}{org_id}:{phone.lstrip('+')}"
            _redis_set(cache_key, property_data, PROPERTY_CACHE_TTL)
        except Exception as exc:
            logger.error("ComplianceEngine.set_active_property: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Extraction helpers
# ──────────────────────────────────────────────────────────────────────────────

def _extract_claims(reply: str) -> PropertyClaim:
    claim = PropertyClaim()
    claim.installments_offered = bool(_INSTALLMENT_RE.search(reply))
    claim.price_amount, claim.price_currency = _extract_price_with_currency(reply)
    city_m = _CITY_RE.search(reply)
    if city_m:
        claim.city = city_m.group(0).strip().lower()
    return claim


def _extract_price_with_currency(text: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Extract the largest price figure and its currency from free text.
    Returns (amount_integer, iso_currency_code_or_None).
    """
    # (amount, currency) tuples
    candidates: list[Tuple[int, Optional[str]]] = []

    def _add(val_str: str, multiplier: int, currency: Optional[str]) -> None:
        try:
            candidates.append((int(Decimal(val_str) * multiplier), currency))
        except (InvalidOperation, ValueError):
            pass

    # South-Asian units → PKR (or INR depending on context, detected separately)
    for m in _CRORE_RE.finditer(text):
        _add(m.group(1), 10_000_000, None)
    for m in _LAKH_RE.finditer(text):
        _add(m.group(1), 100_000, None)

    # International units (no currency implied by unit itself)
    for m in _BILLION_RE.finditer(text):
        _add(m.group(1), 1_000_000_000, None)
    for m in _MILLION_RE.finditer(text):
        _add(m.group(1), 1_000_000, None)
    for m in _THOUSAND_RE.finditer(text):
        _add(m.group(1), 1_000, None)

    # Currency-prefixed raw notations
    def _add_raw(pat: re.Pattern, currency: str) -> None:
        for m in pat.finditer(text):
            try:
                cleaned = re.sub(r'[,\s]', '', m.group(1))
                val = int(Decimal(cleaned))
                if 100 < val < 100_000_000_000:
                    candidates.append((val, currency))
            except (InvalidOperation, ValueError):
                pass

    _add_raw(_RAW_PKR_RE, 'PKR')
    _add_raw(_RAW_AED_RE, 'AED')
    _add_raw(_RAW_USD_RE, 'USD')
    _add_raw(_RAW_GBP_RE, 'GBP')
    _add_raw(_RAW_EUR_RE, 'EUR')
    _add_raw(_RAW_SAR_RE, 'SAR')
    _add_raw(_RAW_INR_RE, 'INR')

    # Suffix-currency: "500,000 AED"
    for m in _SUFFIX_CURRENCY_RE.finditer(text):
        try:
            cleaned = re.sub(r'[,\s]', '', m.group(1))
            val = int(Decimal(cleaned))
            if 100 < val < 100_000_000_000:
                candidates.append((val, m.group(2).upper()))
        except (InvalidOperation, ValueError):
            pass

    if not candidates:
        return None, None

    # Pick the candidate with the largest amount
    best_amount, best_currency = max(candidates, key=lambda t: t[0])

    # Determine currency from word-context if not already set
    if best_currency is None:
        text_lower = text.lower()
        for word, code in _WORD_TO_CURRENCY.items():
            if word in text_lower:
                best_currency = code
                break

    return best_amount, best_currency


def _extract_price_pkr(text: str) -> Optional[int]:
    """Backward-compat wrapper — returns amount regardless of currency."""
    amount, _ = _extract_price_with_currency(text)
    return amount


# ──────────────────────────────────────────────────────────────────────────────
# DB / cache helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_property_by_id(property_id: str) -> Optional[dict]:
    cache_key = f"{_CACHE_PREFIX}id:{property_id}"
    cached    = _redis_get(cache_key)
    if cached:
        return cached
    try:
        from apps.properties.models import Property
        prop = Property.objects.filter(id=property_id, is_active=True).values(
            'id', 'price', 'currency', 'city', 'installment_available',
            'organization_id', 'title',
        ).first()
        if prop:
            prop = dict(prop)
            prop['id']              = str(prop['id'])
            prop['organization_id'] = str(prop.get('organization_id') or '')
            _redis_set(cache_key, prop, PROPERTY_CACHE_TTL)
        return prop
    except Exception as exc:
        logger.error("ComplianceEngine: DB lookup failed for property %s: %s", property_id, exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Violation handlers
# ──────────────────────────────────────────────────────────────────────────────

def _check_price(
    claimed:          int,
    db_price:         int,
    prop:             dict,
    phone:            Optional[str],
    org,
) -> ComplianceResult:
    if db_price == 0:
        return ComplianceResult(safe=True)

    deviation = abs(claimed - db_price) / db_price
    if deviation <= PRICE_VARIANCE_THRESHOLD:
        return ComplianceResult(safe=True)

    details = (
        f"AI claimed {claimed:,} but DB records {db_price:,} "
        f"({deviation:.1%} deviation exceeds {PRICE_VARIANCE_THRESHOLD:.0%} threshold) "
        f"for property {prop.get('id', '?')}"
    )
    logger.error(
        "ComplianceEngine PRICE_VIOLATION phone=%s org=%s prop=%s claimed=%d db=%d deviation=%.2f%%",
        phone, getattr(org, 'id', None), prop.get('id'), claimed, db_price, deviation * 100,
    )
    _persist_violation('price_variance', details, phone, prop, org)
    _reroute_to_agent(phone, prop, org)
    return ComplianceResult(
        safe=False,
        action_taken='blocked_price',
        variance_pct=round(deviation, 4),
        db_price=db_price,
        claimed_price=claimed,
        details=details,
        human_reply=_human_handoff_reply(),
    )


def _block_currency_mismatch(
    claimed_currency: str,
    db_currency:      str,
    prop:             dict,
    phone:            Optional[str],
    org,
) -> ComplianceResult:
    details = (
        f"AI used currency '{claimed_currency}' but DB records '{db_currency}' "
        f"for property {prop.get('id', '?')}"
    )
    logger.error("ComplianceEngine CURRENCY_MISMATCH phone=%s %s", phone, details)
    _persist_violation('currency_mismatch', details, phone, prop, org)
    _reroute_to_agent(phone, prop, org)
    return ComplianceResult(
        safe=False,
        action_taken='blocked_currency_mismatch',
        details=details,
        human_reply=_human_handoff_reply(),
    )


def _block_installment(prop: dict, phone: Optional[str], org) -> ComplianceResult:
    details = (
        f"AI offered installment plan but property {prop.get('id', '?')} "
        f"has installment_available=False in DB"
    )
    logger.error("ComplianceEngine INSTALLMENT_VIOLATION phone=%s prop=%s", phone, prop.get('id'))
    _persist_violation('false_installment', details, phone, prop, org)
    _reroute_to_agent(phone, prop, org)
    return ComplianceResult(
        safe=False,
        action_taken='blocked_installment',
        details=details,
        human_reply=_human_handoff_reply(),
    )


def _block_location(
    claimed_city: str,
    db_city:      str,
    prop:         dict,
    phone:        Optional[str],
    org,
) -> ComplianceResult:
    details = (
        f"AI claimed city '{claimed_city}' but DB records '{db_city}' "
        f"for property {prop.get('id', '?')}"
    )
    logger.error("ComplianceEngine LOCATION_VIOLATION phone=%s %s", phone, details)
    _persist_violation('location_mismatch', details, phone, prop, org)
    _reroute_to_agent(phone, prop, org)
    return ComplianceResult(
        safe=False,
        action_taken='blocked_location',
        details=details,
        human_reply=_human_handoff_reply(),
    )


def _reroute_to_agent(phone: Optional[str], prop: dict, org) -> None:
    if not phone:
        return
    try:
        from django.contrib.auth import get_user_model
        from apps.leads.models import Lead
        User = get_user_model()
        normalized = f"+{phone.lstrip('+')}"
        user = User.objects.filter(phone=normalized).first()
        if not user:
            return
        qs = Lead.objects.filter(user=user)
        if org:
            qs = qs.filter(organization=org)
        updated = qs.update(routing_state=Lead.RoutingState.AGENT_ASSIGNED)
        logger.info(
            "ComplianceEngine: escalated %d lead(s) to AGENT_ASSIGNED phone=%s", updated, phone
        )
    except Exception as exc:
        logger.error("ComplianceEngine _reroute_to_agent: %s", exc)


def _persist_violation(
    violation_type: str,
    details:        str,
    phone:          Optional[str],
    prop:           dict,
    org,
) -> None:
    try:
        from apps.audit.models import ComplianceViolation
        ComplianceViolation.objects.create(
            phone=phone or '',
            organization_id=str(getattr(org, 'id', '') or ''),
            property_id=str(prop.get('id', '') or ''),
            violation_type=violation_type,
            details=details,
        )
    except Exception as exc:
        logger.warning(
            "ComplianceViolation DB write failed (%s) — emitting structured log", exc
        )
        logger.error(
            "COMPLIANCE_VIOLATION type=%s phone=%s prop=%s org=%s details=%s",
            violation_type, phone, prop.get('id'), getattr(org, 'id', None), details,
        )


def _human_handoff_reply() -> str:
    return (
        "I need to connect you with one of our property specialists for accurate details "
        "on this property. An agent will follow up with you shortly.\n\n"
        "Type *menu* to continue browsing, or wait for your agent to reach out. 🏠"
    )


def _redis_get(key: str) -> Optional[dict]:
    try:
        from django.core.cache import cache
        raw = cache.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _redis_set(key: str, value: dict, ttl: int = PROPERTY_CACHE_TTL) -> None:
    try:
        from django.core.cache import cache
        cache.set(key, json.dumps(value, default=str), ttl)
    except Exception:
        pass
