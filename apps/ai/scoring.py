"""AI-powered property analysis, fraud detection, OCR, and batch scoring."""
import json
import logging
import re

logger = logging.getLogger(__name__)

_PROPERTY_SCORE_PROMPT = """You are a Pakistani real estate investment analyst scoring a property.

Property details:
- Title: {title}
- City: {city}
- Location: {location}
- Type: {property_type}
- Area: {area_marla} marla
- Price: PKR {price}
- Legal status: {legal_status}
- Construction status: {construction_status}
- Furnished: {furnished_status}

Pre-computed market signals (use these as anchors):
- Location tier: {location_tier} (tier_1=premium, tier_2=mid, tier_3=developing)
- Price vs area benchmark: {price_signal} (fair/underpriced/overpriced)
- Listing completeness: {completeness_pct}%
- Passed verifications: {passed_verifications}
- Supporting documents submitted: {document_count}
- Deterministic baseline score: {baseline_score}/100

Adjust the baseline score up or down based on your Pakistani real estate expertise.
Consider: location growth potential, price trend, infrastructure, demand, and any red flags.
Keep your final score within 15 points of the baseline unless there is a strong reason.

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "score": <0-100 integer>,
  "risk": "low" | "medium" | "high",
  "factors": ["specific reason 1", "specific reason 2", "specific reason 3"],
  "suggestion": "one-line actionable investor recommendation"
}}
"""


def score_property(property_obj) -> dict:
    """Return AI scoring dict for a property. Raises on AI failure."""
    from apps.properties.scoring import PropertyScoringEngine
    from .client import GeminiClient

    signals  = PropertyScoringEngine.compute_signals(property_obj)
    baseline = PropertyScoringEngine.deterministic_score(signals)

    prompt = _PROPERTY_SCORE_PROMPT.format(
        title=property_obj.title,
        city=property_obj.city,
        location=property_obj.location,
        property_type=property_obj.property_type,
        area_marla=property_obj.area_marla or 0,
        price=property_obj.price or 0,
        legal_status=property_obj.legal_status,
        construction_status=signals['construction_status'],
        furnished_status=signals['furnished_status'],
        location_tier=signals['location_tier'],
        price_signal=signals['price_signal'],
        completeness_pct=signals['completeness_pct'],
        passed_verifications=signals['passed_verifications'],
        document_count=signals['document_count'],
        baseline_score=baseline,
    )

    raw = GeminiClient.generate(
        prompt,
        interaction_type='property_score',
        max_output_tokens=300,
        expect_json=True,
    )
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    return json.loads(m.group(0)) if m else json.loads(raw)


_FRAUD_CHECK_PROMPT = """You are a Pakistani real estate fraud detection assistant.

Input: "{query}"

Check for red flags such as:
- Unregistered/blacklisted agents
- Disputed registries
- Common scam patterns (fake plot files, double sale, etc.)

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "risk": "low" | "medium" | "high",
  "flags": ["flag 1", "flag 2"],
  "recommendation": "<one paragraph max>",
  "verify_steps": ["actionable step 1", "actionable step 2"]
}}
"""

_BATCH_VERDICTS_PROMPT = """You are a Pakistani real estate advisor. Give a one-line investment verdict for each listing.

Listings:
{listings}

Be concise, practical, and specific to the Pakistani market.

Output ONLY a JSON object. No prose, no markdown fences.
Schema: {{"verdicts": [{{"id": "<source_id>", "verdict": "<one sentence max>"}}]}}
"""

_OCR_PROMPT = """You are extracting structured data from a Pakistani property document image.

Extract the following fields if present:
- registry_number
- owner_name
- cnic
- property_address
- area_marla
- property_type
- mutation_date
- registrar_office

If a field isn't visible, use null. Also flag anything that looks tampered or inconsistent.

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "fields": {{}},
  "tamper_flags": [],
  "overall_confidence": 0.0
}}
"""


def fraud_check(query: str) -> dict:
    """Return AI fraud risk assessment dict for a query string."""
    from .client import GeminiClient
    raw = GeminiClient.generate(
        _FRAUD_CHECK_PROMPT.format(query=query),
        interaction_type='fraud_check',
        max_output_tokens=400,
        expect_json=True,
    )
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    return json.loads(m.group(0)) if m else json.loads(raw)


def batch_verdicts(results: list) -> dict:
    """Return {source_id: verdict} dict for up to 5 search results."""
    from .client import GeminiClient
    lines = []
    for i, r in enumerate(results[:5], 1):
        price = f"PKR {r.price:,}" if r.price else "price unknown"
        area  = f"{r.area_marla}M" if r.area_marla else ""
        lines.append(f"{i}. [{r.source_id}] {r.title} | {r.city}, {r.location} | {area} | {price}")
    raw = GeminiClient.generate(
        _BATCH_VERDICTS_PROMPT.format(listings="\n".join(lines)),
        interaction_type='property_score',
        max_output_tokens=500,
        expect_json=True,
    )
    try:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else json.loads(raw)
        return {item['id']: item['verdict'] for item in data.get('verdicts', [])}
    except Exception:
        return {}


def ocr_document(image_bytes: bytes, mime_type: str = 'image/jpeg') -> str:
    """OCR a property document image. Returns raw text or JSON string."""
    from .client import GeminiClient
    return GeminiClient.vision(_OCR_PROMPT, image_bytes, mime_type=mime_type)
