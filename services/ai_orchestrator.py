from apps.ai.client import GeminiClient
from .prompt_library import render


class AIOrchestrator:

    # @classmethod
    # def classify_intent(cls, message: str, user=None) -> dict:
    #     prompt = render('intent_classify', message=message)
    #     return GeminiClient.generate_json(
    #         prompt, user=user, interaction_type='intent_classify',
    #         max_output_tokens=128,
    #     )
    
    @classmethod
    def classify_intent(cls, message: str, user=None) -> dict:
        prompt = render('intent_classify', message=message)
        return GeminiClient.generate_json(
            prompt, user=user, interaction_type='intent_classify',
            max_output_tokens=256,   # was 128 — too tight
            temperature=0.1,
        )

    @classmethod
    def score_property(cls, property_obj, user=None) -> dict:
        prompt = render(
            'property_score',
            title=property_obj.title,
            city=property_obj.city,
            location=property_obj.location,
            property_type=property_obj.property_type,
            area_marla=property_obj.area_marla or 0,
            price_pkr=property_obj.price_pkr or 0,
            legal_status=property_obj.legal_status,
        )
        return GeminiClient.generate_json(
            prompt, user=user, interaction_type='property_score',
            max_output_tokens=300,
        )

    @classmethod
    def tax_7e(cls, fmv: int, filer_status: str, properties_count: int = 1, user=None) -> dict:
        prompt = render(
            'tax_7e_advisor',
            fmv=fmv, filer_status=filer_status, properties_count=properties_count,
        )
        return GeminiClient.generate_json(
            prompt, user=user, interaction_type='tax_advisory',
            max_output_tokens=300,
        )

    @classmethod
    def loan_eligibility(cls, monthly_income: int, loan_amount: int,
                         tenure_years: int, existing_emi: int = 0, user=None) -> dict:
        prompt = render(
            'loan_eligibility',
            monthly_income=monthly_income,
            loan_amount=loan_amount,
            tenure_years=tenure_years,
            existing_emi=existing_emi,
        )
        return GeminiClient.generate_json(
            prompt, user=user, interaction_type='loan_check',
            max_output_tokens=400,
        )

    @classmethod
    def fraud_check(cls, query: str, user=None) -> dict:
        prompt = render('fraud_check', query=query)
        return GeminiClient.generate_json(
            prompt, user=user, interaction_type='fraud_check',
            max_output_tokens=400,
        )

    @classmethod
    def ocr_document(cls, image_bytes: bytes, mime_type: str = 'image/jpeg') -> str:
        return GeminiClient.vision(
            render('ocr_property_doc'),
            image_bytes,
            mime_type=mime_type,
        )

    @classmethod
    def transcribe_voice(cls, audio_bytes: bytes, mime_type: str = 'audio/ogg') -> str:
        return GeminiClient.transcribe_audio(audio_bytes, mime_type)

    @classmethod
    def batch_verdicts(cls, results: list, user=None) -> dict:
        """
        One Gemini call to score up to 5 property listings.
        Returns {source_id: verdict_text}.
        """
        lines = []
        for i, r in enumerate(results[:5], 1):
            price = f"PKR {r.price_pkr:,}" if r.price_pkr else "price unknown"
            area  = f"{r.area_marla}M" if r.area_marla else ""
            lines.append(
                f"{i}. [{r.source_id}] {r.title} | {r.city}, {r.location} | {area} | {price}"
            )
        listings_text = "\n".join(lines)
        prompt = render('batch_verdicts', listings=listings_text)
        try:
            raw = GeminiClient.generate_json(
                prompt, user=user, interaction_type='property_score',
                max_output_tokens=500,
            )
            return {item['id']: item['verdict'] for item in raw.get('verdicts', [])}
        except Exception:
            return {}