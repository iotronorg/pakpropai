"""
AI-powered property analysis, fraud detection, OCR and batch verdicts.

T-1120: prompts live in the registry (apps/ai/prompts.py: property.score,
fraud.check, search.verdicts, document.ocr) and calls go through apps.ai.llm
(provider failover, prompt version logged). Signatures are unchanged.
"""
import logging

from . import llm

logger = logging.getLogger(__name__)


def score_property(property_obj) -> dict:
    """Return AI scoring dict for a property. Raises on AI failure."""
    from apps.properties.scoring import PropertyScoringEngine

    signals  = PropertyScoringEngine.compute_signals(property_obj)
    baseline = PropertyScoringEngine.deterministic_score(signals)
    data = llm.complete_json('property.score', {
        'title': property_obj.title, 'city': property_obj.city, 'location': property_obj.location,
        'property_type': property_obj.property_type, 'area': property_obj.size_display() or 'unknown',
        'price': property_obj.price or 0, 'legal_status': property_obj.legal_status,
        'construction_status': signals['construction_status'],
        'furnished_status': signals['furnished_status'], 'location_tier': signals['location_tier'],
        'price_signal': signals['price_signal'], 'completeness_pct': signals['completeness_pct'],
        'passed_verifications': signals['passed_verifications'],
        'document_count': signals['document_count'], 'baseline_score': baseline,
        **({'currency': property_obj.currency} if getattr(property_obj, 'currency', None) else {}),
    }, country=property_obj.country, interaction_type='property_score', max_tokens=300)
    if not isinstance(data, dict) or not data:
        raise ValueError('AI score reply had no JSON object')
    return data


def fraud_check(query: str, country: str | None = None) -> dict:
    """Return AI fraud risk assessment dict for a query string."""
    data = llm.complete_json('fraud.check', {'query': query}, country=country,
                             interaction_type='fraud_check', max_tokens=400)
    if not isinstance(data, dict) or not data:
        raise ValueError('AI fraud reply had no JSON object')
    return data


def batch_verdicts(results: list, country: str | None = None) -> dict:
    """Return {source_id: verdict} dict for up to 5 search results."""
    from apps.markets.services import format_price
    lines = []
    for i, r in enumerate(results[:5], 1):
        price = format_price(r.price, getattr(r, 'currency', None), missing="price unknown")
        size = getattr(r, 'size_display', None)
        area = (size() if callable(size) else '') or (f"{r.area_marla}M" if getattr(r, 'area_marla', None) else '')
        lines.append(f"{i}. [{r.source_id}] {r.title} | {r.city}, {r.location} | {area} | {price}")
    try:
        data = llm.complete_json('search.verdicts', {'listings': "\n".join(lines)}, country=country,
                                 interaction_type='property_score', max_tokens=500)
        return {item['id']: item['verdict'] for item in data.get('verdicts', [])}
    except Exception:
        return {}


def ocr_document(image_bytes: bytes, mime_type: str = 'image/jpeg', country: str | None = None) -> str:
    """OCR a property document image. Returns raw text or JSON string."""
    return llm.vision('document.ocr', image_bytes, mime_type, country=country).text
