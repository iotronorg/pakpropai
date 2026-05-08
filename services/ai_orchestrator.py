"""
AIOrchestrator — handles non-conversational structured AI tasks.
(property scoring, document OCR, voice transcription)

Conversational WhatsApp interactions go through apps.ai.agent.PakPropAgent.
"""
import hashlib
import json
from django.core.cache import cache
from apps.ai.client import GeminiClient
from .prompt_library import render

_AI_CACHE_TTL = 3600       # 1 hour — conversational / scoring
_TAX_CACHE_TTL = 86400     # 24 hours — pure math, deterministic


def _cache_key(namespace: str, **inputs) -> str:
    raw = json.dumps(inputs, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"ai:{namespace}:{digest}"


def _cached_call(cache_key: str, ttl: int, fn, *args, **kwargs):
    result = cache.get(cache_key)
    if result is not None:
        return result
    result = fn(*args, **kwargs)
    cache.set(cache_key, result, ttl)
    return result


class AIOrchestrator:

    @classmethod
    def classify_intent(cls, message: str, user=None) -> dict:
        key = _cache_key('intent', message=message)
        prompt = render('intent_classify', message=message)
        return _cached_call(
            key, _AI_CACHE_TTL,
            GeminiClient.generate_json,
            prompt, user=user, interaction_type='intent_classify',
            max_output_tokens=256, temperature=0.1,
        )

    @classmethod
    def score_property(cls, property_obj, user=None) -> dict:
        from apps.properties.scoring import PropertyScoringEngine
        signals       = PropertyScoringEngine.compute_signals(property_obj)
        baseline      = PropertyScoringEngine.deterministic_score(signals)
        prompt = render(
            'property_score',
            title=property_obj.title,
            city=property_obj.city,
            location=property_obj.location,
            property_type=property_obj.property_type,
            area_marla=property_obj.area_marla or 0,
            price_pkr=property_obj.price_pkr or 0,
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
        return GeminiClient.generate_json(
            prompt, user=user, interaction_type='property_score',
            max_output_tokens=300,
        )

    @classmethod
    def tax_7e(cls, fmv: int, filer_status: str,
               properties_count: int = 1, user=None) -> dict:
        key = _cache_key('tax7e', fmv=fmv, filer_status=filer_status, count=properties_count)
        prompt = render(
            'tax_7e_advisor',
            fmv=fmv, filer_status=filer_status,
            properties_count=properties_count,
        )
        return _cached_call(
            key, _TAX_CACHE_TTL,
            GeminiClient.generate_json,
            prompt, user=user, interaction_type='tax_advisory',
            max_output_tokens=300,
        )

    @classmethod
    def loan_eligibility(cls, monthly_income: int, loan_amount: int,
                         tenure_years: int, existing_emi: int = 0, user=None) -> dict:
        key = _cache_key('loan', income=monthly_income, amount=loan_amount,
                         tenure=tenure_years, emi=existing_emi)
        prompt = render(
            'loan_eligibility',
            monthly_income=monthly_income,
            loan_amount=loan_amount,
            tenure_years=tenure_years,
            existing_emi=existing_emi,
        )
        return _cached_call(
            key, _TAX_CACHE_TTL,
            GeminiClient.generate_json,
            prompt, user=user, interaction_type='loan_check',
            max_output_tokens=400,
        )

    @classmethod
    def fraud_check(cls, query: str, user=None) -> dict:
        key = _cache_key('fraud', query=query)
        prompt = render('fraud_check', query=query)
        return _cached_call(
            key, _AI_CACHE_TTL,
            GeminiClient.generate_json,
            prompt, user=user, interaction_type='fraud_check',
            max_output_tokens=400,
        )

    @classmethod
    def ocr_document(cls, image_bytes: bytes,
                     mime_type: str = 'image/jpeg') -> str:
        return GeminiClient.vision(
            render('ocr_property_doc'),
            image_bytes,
            mime_type=mime_type,
        )

    @classmethod
    def transcribe_voice(cls, audio_bytes: bytes,
                         mime_type: str = 'audio/ogg') -> str:
        return GeminiClient.transcribe_audio(audio_bytes, mime_type)

    @classmethod
    def batch_verdicts(cls, results: list, user=None) -> dict:
        lines = []
        for i, r in enumerate(results[:5], 1):
            price = f"PKR {r.price_pkr:,}" if r.price_pkr else "price unknown"
            area  = f"{r.area_marla}M" if r.area_marla else ""
            lines.append(
                f"{i}. [{r.source_id}] {r.title} | {r.city}, {r.location} | {area} | {price}"
            )
        prompt = render('batch_verdicts', listings="\n".join(lines))
        try:
            raw = GeminiClient.generate_json(
                prompt, user=user, interaction_type='property_score',
                max_output_tokens=500,
            )
            return {item['id']: item['verdict'] for item in raw.get('verdicts', [])}
        except Exception:
            return {}
