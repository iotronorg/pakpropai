"""Automated checks before a listing goes public (FR-LST-009). Codes only; a person decides."""
from __future__ import annotations

import re
import statistics

from apps.properties.models import Property

OUTLIER = 5          # times above or below the median price per m²
MIN_COMPARABLES = 10
# Refusing people by religion, ethnicity, origin or family status. Not a judgement of style.
_PROHIBITED = re.compile(
    r'\b(?:(?:muslims?|christians?|hindus?|sikhs?|jew(?:s|ish)?|shias?|sunnis?|ahmadis?|punjabis?|pathans?|pashtuns?|'
    r'sindhis?|muhajirs?|arabs?|indians?|whites?|blacks?|asians?|westerners?|europeans?)\s+only|'
    r'no\s+(?:muslims?|christians?|hindus?|sikhs?|jews|shias?|sunnis?|ahmadis?|punjabis?|pathans?|pashtuns?|sindhis?|'
    r'muhajirs?|arabs?|indians?|blacks?|asians?|foreigners?|bachelors?|kids|children))\b', re.I)
_LINK = re.compile(r'https?://|\bwww\.|\b(?:bit\.ly|t\.me|wa\.me)/', re.I)


def _price_outlier(p: Property) -> bool:
    if p.price_on_request or not p.price or not p.size_m2 or p.price_type == Property.PriceType.PER_SQM:
        return False
    rows = (Property.objects.visible_to(None).exclude(pk=p.pk)
            .filter(city__iexact=p.city, purpose=p.purpose, category=p.category, price_type=p.price_type,
                    currency=p.currency, price_on_request=False, price__gt=0, size_m2__gt=0)
            .order_by('-created_at').values_list('price', 'size_m2')[:200])
    each = [float(price) / float(size) for price, size in rows]
    if len(each) < MIN_COMPARABLES:
        return False
    median, own = statistics.median(each), float(p.price) / float(p.size_m2)
    return own > median * OUTLIER or own < median / OUTLIER


def _duplicate(p: Property) -> bool:
    if not p.price or p.size_value is None or not (p.location or '').strip():
        return False
    return (Property.objects.visible_to(None).exclude(pk=p.pk)
            .filter(city__iexact=p.city, location__iexact=p.location, purpose=p.purpose, category=p.category,
                    price=p.price, currency=p.currency, size_value=p.size_value, size_unit=p.size_unit,
                    bedrooms=p.bedrooms, floor=p.floor, title__iexact=p.title).exists())


def run(p: Property) -> list[str]:
    from apps.properties.lifecycle import market_settings
    text = f'{p.title or ""}\n{p.description or ""}'
    terms = market_settings(p).get('prohibited_terms')
    terms = [t.lower() for t in terms if isinstance(t, str) and t.strip()] if isinstance(terms, list) else []
    out = []
    if _PROHIBITED.search(text) or any(re.search(rf'(?<!\w){re.escape(t)}(?!\w)', text.lower()) for t in terms):
        out.append('prohibited_content')
    if _LINK.search(text):
        out.append('external_link')
    from apps.ai.untrusted import suspicious
    if suspicious(text):                     # orders to the assistant (T-2071)
        out.append('assistant_instructions')
    if _price_outlier(p):
        out.append('price_outlier')
    if _duplicate(p):
        out.append('possible_duplicate')
    from apps.properties.duplicates import signals
    out.extend(signals(p))                  # T-4050: image_reused, reposted_after_takedown
    return out
