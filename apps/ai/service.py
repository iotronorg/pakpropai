"""
AIServiceManager — orchestration layer for the RealTron AI platform.

Architecture
────────────
  MessageRouter  ──►  AIServiceManager.process()
                              │
                    ┌─────────▼──────────┐
                    │  1. Guardrail check │  (rejects off-topic / bad length)
                    └─────────┬──────────┘
                    ┌─────────▼──────────┐
                    │  2. Intent classify │  (deterministic regex, no LLM)
                    └─────────┬──────────┘
                         confidence ≥ 0.85?
                    ┌─────────┴──────────┐
                   YES                  NO
                    │                    │
          ┌─────────▼──────┐   ┌─────────▼──────────────────┐
          │  Direct route  │   │  DynamicContextBuilder      │
          │  (pure Python  │   │  → enhanced system prompt   │
          │   tool call)   │   │  → RealTronAgent.chat()     │
          └─────────┬──────┘   └─────────┬──────────────────┘
                    └────────────┬────────┘
                    ┌────────────▼────────┐
                    │  3. Output guard     │  (fallback on empty / failure)
                    └─────────────────────┘

Direct routing bypasses the LLM entirely for high-confidence deterministic
intents (scam check, tax calc).  Property search still calls the LLM for
natural-language result formatting, but with normalised params pre-injected.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from apps.core.metrics import ai_requests_total, ai_request_duration_seconds

logger = logging.getLogger(__name__)

# Lazy imports — heavy modules are only loaded when needed
_CRORE_RE  = re.compile(r'(\d+(?:\.\d+)?)\s*crore',    re.I)
_LAKH_RE   = re.compile(r'(\d+(?:\.\d+)?)\s*lakh',     re.I)
_MARLA_RE  = re.compile(r'(\d+(?:\.\d+)?)\s*marla',    re.I)
_KANAL_RE  = re.compile(r'(\d+(?:\.\d+)?)\s*kanal',    re.I)
_SQFT_RE   = re.compile(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:sqft|sq\.?\s*ft)',  re.I)
_URL_RE    = re.compile(r'https?://\S+', re.I)

# Cities — extended as platform expands globally
_CITY_MAP: dict[str, str] = {
    'lahore': 'Lahore', 'karachi': 'Karachi', 'islamabad': 'Islamabad',
    'rawalpindi': 'Rawalpindi', 'peshawar': 'Peshawar', 'quetta': 'Quetta',
    'multan': 'Multan', 'faisalabad': 'Faisalabad', 'sialkot': 'Sialkot',
    'gujranwala': 'Gujranwala', 'hyderabad': 'Hyderabad', 'bahawalpur': 'Bahawalpur',
    'abbottabad': 'Abbottabad', 'sargodha': 'Sargodha', 'sukkur': 'Sukkur',
    'dubai': 'Dubai', 'abu dhabi': 'Abu Dhabi', 'sharjah': 'Sharjah',
    'london': 'London', 'manchester': 'Manchester',
}

_LOCATION_KEYWORDS = [
    'dha', 'bahria', 'gulberg', 'defence', 'model town', 'johar town',
    'clifton', 'pechs', 'f-6', 'f-7', 'f-8', 'f-10', 'f-11',
    'g-11', 'g-13', 'i-8', 'i-10', 'e-11', 'd-12', 'b-17',
    'cantt', 'cantonment', 'blue area', 'saddar', 'mall road',
    'phase 1', 'phase 2', 'phase 3', 'phase 4', 'phase 5',
    'phase 6', 'phase 7', 'phase 8',
]

_PROP_TYPE_MAP: dict[re.Pattern, str] = {
    re.compile(r'\b(plot|land|piece of land|zameen)\b',             re.I): 'plot',
    re.compile(r'\b(house|home|villa|bungalow|makaan|ghar|kothi)\b', re.I): 'residential',
    re.compile(r'\b(flat|apartment|studio|floor)\b',                 re.I): 'residential',
    re.compile(r'\b(shop|office|warehouse|commercial|plaza)\b',      re.I): 'commercial',
}

_TAX_KEYWORDS = re.compile(
    r'\b(7e|section 7e|cvt|capital value tax|cgt|capital gain|'
    r'property tax|fbr tax|annual tax|withholding tax|wht|stamp duty|'
    r'rental tax|income from rent|tax on rent|'
    r'filer|non.?filer)\b',
    re.I,
)
_FILER_RE = re.compile(r'\b(non.?filer|non filer)\b', re.I)

_FRAUD_KEYWORDS = re.compile(
    r'\b(scam|fraud|fake|verify|legit|trust|'
    r'kachhi file|kachi file|kachha file|'
    r'double sale|forged|power of attorney|'
    r'advance payment|token first|overseas seller|'
    r'check this deal|is this safe|suspicious)\b',
    re.I,
)

_LOAN_KEYWORDS = re.compile(
    r'\b(loans?|mortgage|emi|apna ghar|home.?finance|bank.?finance|'
    r'down.?payment|monthly installment|qist)\b',
    re.I,
)

_SEARCH_INTENT_KEYWORDS = re.compile(
    r'\b(search|find|looking for|want to buy|want to rent|dhundh|'
    r'chahiye|chahta|property for sale|for rent|available)\b',
    re.I,
)

_DEAL_LOCK_KEYWORDS = re.compile(
    r'\b(deal.?lock|token.?payment|book|reserve|hold property|lock property|'
    r'paka karo|book karana|advance de|token de|payment first|'
    r'pakka|confirm deal|seal deal)\b',
    re.I,
)

_DOCUMENT_KEYWORDS = re.compile(
    r'\b(verify document|check document|ownership letter|allotment letter|'
    r'registry|fard|intiqal|mutation|noc|title deed|property paper|'
    r'khasra|khata|bayana|sale deed|verify ownership|'
    r'document check|papers check|kaghaz check)\b',
    re.I,
)

_DIRECT_ROUTE_CONFIDENCE = 0.85

# Cities outside Pakistan whose audit requests must fall to LLM (non-PKR market)
_NON_PK_CITIES = {
    'dubai', 'abu dhabi', 'sharjah', 'ajman', 'london', 'manchester',
    'birmingham', 'new york', 'los angeles', 'toronto', 'sydney',
}


# ── Intent Classifier ─────────────────────────────────────────────────────────

class IntentClassifier:
    """
    Deterministic, regex-based intent pre-classifier.
    No LLM call — pure Python.  Fast, inspectable, and 100% reproducible.

    Returns IntentResult with confidence 0–1:
      ≥ 0.85  → direct Python routing (bypasses LLM for deterministic tools)
      < 0.85  → full LLM call with normalised query injected
    """

    @classmethod
    def classify(cls, message: str, history: list | None = None) -> 'IntentResult':
        from apps.ai.schemas import IntentResult

        msg   = message.strip()
        lower = msg.lower()

        # ── Greeting ───────────────────────────────────────────────────────────
        if cls._is_greeting(lower):
            return IntentResult(intent='greeting', confidence=1.0)

        # ── Language detection ─────────────────────────────────────────────────
        language = cls._detect_language(lower)

        # ── Scam / fraud check ─────────────────────────────────────────────────
        if _FRAUD_KEYWORDS.search(lower):
            scam_input = cls._extract_scam_input(msg)
            if scam_input:
                return IntentResult(
                    intent='scam_check',
                    confidence=0.90,
                    language=language,
                    scam_input=scam_input,
                )

        # ── Tax advice ─────────────────────────────────────────────────────────
        if _TAX_KEYWORDS.search(lower):
            tax_input = cls._extract_tax_input(msg)
            if tax_input:
                return IntentResult(
                    intent='tax_advice',
                    confidence=0.88,
                    language=language,
                    tax_input=tax_input,
                )

        # ── Loan eligibility ───────────────────────────────────────────────────
        if _LOAN_KEYWORDS.search(lower):
            loan_input = cls._extract_loan_input(msg)
            if loan_input:
                return IntentResult(
                    intent='loan_eligibility',
                    confidence=0.87,
                    language=language,
                    loan_input=loan_input,
                )
            return IntentResult(
                intent='loan_eligibility',
                confidence=0.80,
                language=language,
                normalised_query=msg,
            )

        # ── Deal lock ──────────────────────────────────────────────────────────
        if _DEAL_LOCK_KEYWORDS.search(lower):
            return IntentResult(
                intent='deal_lock',
                confidence=0.80,
                language=language,
                normalised_query=msg,
            )

        # ── Document verification (text-only) ─────────────────────────────────
        if _DOCUMENT_KEYWORDS.search(lower):
            return IntentResult(
                intent='document_verify_text',
                confidence=0.75,
                language=language,
                normalised_query=msg,
            )

        # ── Property audit — checked BEFORE property_search so audit messages
        #    containing city/size details don't get misrouted as searches.
        if re.search(r'\b(audit|report|analysis|detailed.?check)\b', lower, re.I):
            audit_input = cls._extract_audit_input(msg, lower, history)
            if audit_input:
                return IntentResult(
                    intent='property_audit',
                    confidence=0.87,
                    language=language,
                    audit_input=audit_input,
                )
            return IntentResult(
                intent='property_audit', confidence=0.75, language=language,
            )

        # ── Property search ────────────────────────────────────────────────────
        city = cls._extract_city(lower, history)
        if city or _SEARCH_INTENT_KEYWORDS.search(lower):
            search_filter = cls._extract_search_filter(msg, lower, city)
            confidence    = 0.90 if (city and search_filter.area_marla) else 0.70
            return IntentResult(
                intent='property_search',
                confidence=confidence,
                language=language,
                search_filter=search_filter,
                normalised_query=cls._normalise_search_query(search_filter),
            )

        # ── List property ──────────────────────────────────────────────────────
        if re.search(r'\b(list|sell|bechna|bechna hai|apni property|my property)\b', lower, re.I):
            return IntentResult(
                intent='list_property', confidence=0.75, language=language,
            )

        # ── Talk to agent ──────────────────────────────────────────────────────
        if re.search(r'\b(agent|broker|dealer|banda|kisi se baat)\b', lower, re.I):
            return IntentResult(
                intent='talk_to_agent', confidence=0.75, language=language,
            )

        # ── General real-estate query ──────────────────────────────────────────
        return IntentResult(
            intent='general_query', confidence=0.50, language=language,
        )

    # ── Extraction helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _is_greeting(lower: str) -> bool:
        _GREET = {
            'hi', 'hello', 'hey', 'salam', 'salaam', 'aoa',
            'assalamualaikum', 'start', 'help', 'menu',
        }
        return lower.rstrip('!?.') in _GREET

    @staticmethod
    def _detect_language(lower: str) -> str:
        urdu_signals = re.compile(
            r'\b(hai|hain|kya|kaise|chahiye|chahta|mujhe|aap|agar|'
            r'ghar|makaan|zameen|marla|bechna|khareedna|kiraya)\b'
        )
        en_count  = len(re.findall(r'[a-z]', lower))
        ur_count  = len(urdu_signals.findall(lower))
        if ur_count >= 2:
            return 'ur' if en_count < len(lower) * 0.6 else 'mixed'
        return 'en'

    @staticmethod
    def _extract_city(lower: str, history: list | None) -> str:
        for key, name in _CITY_MAP.items():
            if key in lower:
                return name
        # Scan last 6 history turns for a city mention
        if history:
            recent = ' '.join(m.get('text', '').lower() for m in history[-6:])
            for key, name in _CITY_MAP.items():
                if key in recent:
                    return name
        return ''

    @classmethod
    def _extract_search_filter(cls, msg: str, lower: str, city: str) -> 'PropertySearchFilter':
        from apps.ai.schemas import PropertySearchFilter

        # Area
        area_marla: Optional[float] = None
        m = _KANAL_RE.search(lower)
        if m:
            area_marla = float(m.group(1)) * 20    # 1 Kanal = 20 Marla
        else:
            m = _MARLA_RE.search(lower)
            if m:
                area_marla = float(m.group(1))
            else:
                m = _SQFT_RE.search(lower)
                if m:
                    area_marla = float(m.group(1).replace(',', '')) / 272.25

        # Price
        price_max: Optional[int] = None
        m = _CRORE_RE.search(lower)
        if m:
            price_max = int(float(m.group(1)) * 10_000_000)
        else:
            m = _LAKH_RE.search(lower)
            if m:
                price_max = int(float(m.group(1)) * 100_000)

        # Property type
        prop_type = ''
        for pattern, ptype in _PROP_TYPE_MAP.items():
            if pattern.search(lower):
                prop_type = ptype
                break

        # Location extraction — stop only at explicit price/size words, not all digits
        # (preserves "DHA Phase 5", "F-7/4", "Sector B-17" which contain digits)
        _LOC_STOP = re.compile(
            r'(?:\s+(?:under|below|above|less\s+than|more\s+than|for\s+(?:sale|rent))|'
            r'\s+\d+(?:\.\d+)?\s*(?:crore|lakh|marla|kanal|sqft))',
            re.I,
        )
        location = ''
        for kw in _LOCATION_KEYWORDS:
            if kw in lower:
                idx     = lower.find(kw)
                snippet = msg[idx: idx + 35]
                stop_m  = _LOC_STOP.search(snippet)
                location = (snippet[:stop_m.start()] if stop_m else snippet).strip().split(',')[0]
                break

        # Purpose
        purpose = 'rent' if re.search(r'\b(rent|rental|kiraya|lease)\b', lower, re.I) else 'buy'

        return PropertySearchFilter(
            city=city or 'Unknown',
            location=location,
            property_type=prop_type,
            area_marla=round(area_marla, 2) if area_marla else None,
            price_max_pkr=price_max,
            purpose=purpose,
        )

    @staticmethod
    def _extract_scam_input(msg: str) -> Optional['ScamCheckInput']:
        from apps.ai.schemas import ScamCheckInput
        lower = msg.lower()
        try:
            url   = _URL_RE.search(msg)
            adv   = bool(re.search(r'\b(advance|token first|pehle paise|agay payment)\b', lower, re.I))
            no_doc = bool(re.search(r'\b(no documents|documents nahi|fard nahi)\b', lower, re.I))
            return ScamCheckInput(
                description=msg[:800],
                url=url.group(0) if url else None,
                advance_payment_demanded=adv,
                documents_available=not no_doc,
            )
        except Exception:
            return None

    @staticmethod
    def _extract_tax_input(msg: str) -> Optional['TaxAdviceInput']:
        from apps.ai.schemas import TaxAdviceInput, CRORE_TO_PKR, LAKH_TO_PKR
        lower = msg.lower()
        try:
            # Value
            fmv: Optional[int] = None
            m = _CRORE_RE.search(lower)
            if m:
                fmv = int(float(m.group(1)) * CRORE_TO_PKR)
            else:
                m = _LAKH_RE.search(lower)
                if m:
                    fmv = int(float(m.group(1)) * LAKH_TO_PKR)
            if not fmv:
                return None

            filer = 'non_filer' if _FILER_RE.search(lower) else 'filer'

            advice_type = 'general'
            if re.search(r'\b(cgt|capital gain|selling|bechna)\b', lower, re.I):
                advice_type = 'cgt'
            elif re.search(r'\b(7e|cvt|annual tax|fbr)\b', lower, re.I):
                advice_type = '7e'
            elif re.search(r'\b(rent|rental|kiraya)\b', lower, re.I):
                advice_type = 'rental'
            elif re.search(r'\b(wht|withholding|stamp duty)\b', lower, re.I):
                advice_type = 'wht'

            yrs_m = re.search(r'(\d+)\s*(?:year|yr|saal)', lower, re.I)
            return TaxAdviceInput(
                fmv_pkr=fmv,
                filer_status=filer,
                advice_type=advice_type,
                holding_years=int(yrs_m.group(1)) if yrs_m else None,
            )
        except Exception:
            return None

    @staticmethod
    def _extract_loan_input(msg: str) -> Optional['LoanEligibilityInput']:
        from apps.ai.schemas import LoanEligibilityInput
        lower = msg.lower()

        def _to_pkr(val: str, unit: str) -> int:
            v = float(val.replace(',', ''))
            u = unit.lower()
            return int(v * (10_000_000 if u == 'crore' else 100_000 if u == 'lakh' else 1_000 if u == 'k' else 1))

        income = None
        m = re.search(
            r'(?:income|earn(?:ing|s)?|salary|tankhwa|mahana)\s*(?:is|hai|of|:|=)?\s*'
            r'(\d[\d,]*(?:\.\d+)?)\s*(k|lakh|crore)?',
            lower, re.I,
        )
        if m:
            income = _to_pkr(m.group(1), m.group(2) or '')

        loan_amount = None
        m = re.search(
            r'(?:loan|mortgage|finance|need|chahiye|borrow|lena)\s*'
            r'(?:of|for|:|=)?\s*(\d[\d,]*(?:\.\d+)?)\s*(lakh|crore|k)?',
            lower, re.I,
        )
        if m:
            loan_amount = _to_pkr(m.group(1), m.group(2) or '')

        # Fallback: if loan amount still not found, pick the largest crore/lakh amount
        # that isn't the income value.
        if not loan_amount:
            candidates = []
            for pat, mult in ((_CRORE_RE, 10_000_000), (_LAKH_RE, 100_000)):
                for hit in pat.finditer(lower):
                    candidates.append(int(float(hit.group(1)) * mult))
            if candidates and income:
                diff = [c for c in candidates if abs(c - income) / max(income, 1) > 0.05]
                if diff:
                    loan_amount = max(diff)
            elif candidates:
                loan_amount = max(candidates)

        if not income or not loan_amount:
            return None

        tenure = 20
        m = re.search(r'(\d+)\s*(?:year|yr|saal)', lower, re.I)
        if m:
            tenure = max(1, min(30, int(m.group(1))))

        scheme = 'apna_ghar' if re.search(r'\b(apna.?ghar|mera.?pakistan)\b', lower, re.I) else 'conventional'
        try:
            return LoanEligibilityInput(
                monthly_income=income, loan_amount=loan_amount,
                tenure_years=tenure, scheme=scheme,
            )
        except Exception:
            return None

    @classmethod
    def _extract_audit_input(cls, msg: str, lower: str, history=None) -> Optional['AuditInput']:
        from apps.ai.schemas import AuditInput

        city = cls._extract_city(lower, history)
        if not city:
            return None

        # Non-PK markets: let the LLM handle with market-appropriate context
        if city.lower() in _NON_PK_CITIES:
            return None

        value = None

        m = _CRORE_RE.search(lower)
        if m:
            value = int(float(m.group(1)) * 10_000_000)
        else:
            m = _LAKH_RE.search(lower)
            if m:
                value = int(float(m.group(1)) * 100_000)
        if not value:
            return None

        _LOC_STOP = re.compile(
            r'(?:\s+(?:under|below|above)|\s+\d+(?:\.\d+)?\s*(?:crore|lakh|marla|kanal|sqft))',
            re.I,
        )
        location = ''
        for kw in _LOCATION_KEYWORDS:
            if kw in lower:
                idx = lower.find(kw)
                snippet = msg[idx: idx + 35]
                stop_m = _LOC_STOP.search(snippet)
                location = (snippet[:stop_m.start()] if stop_m else snippet).strip().split(',')[0]
                break
        if not location:
            location = city  # default to city when no area keyword found

        prop_type = 'residential'
        for pattern, ptype in _PROP_TYPE_MAP.items():
            if pattern.search(lower):
                prop_type = ptype
                break

        area_marla = None
        m = _KANAL_RE.search(lower)
        if m:
            area_marla = round(float(m.group(1)) * 20, 2)
        else:
            m = _MARLA_RE.search(lower)
            if m:
                area_marla = round(float(m.group(1)), 2)

        try:
            return AuditInput(
                city=city, location=location, property_type=prop_type,
                estimated_value_pkr=value, area_marla=area_marla,
            )
        except Exception:
            return None

    @staticmethod
    def _normalise_search_query(sf: 'PropertySearchFilter') -> str:
        """Produce a compact normalised string the LLM can act on without ambiguity."""
        parts = [f"city={sf.city}"]
        if sf.location:
            parts.append(f"location={sf.location}")
        if sf.property_type:
            parts.append(f"type={sf.property_type}")
        if sf.area_marla:
            parts.append(f"area={sf.area_marla}M ({sf.area_sqft} sqft)")
        if sf.price_max_pkr:
            parts.append(f"max_price=PKR {sf.price_max_pkr:,}")
        if sf.purpose:
            parts.append(f"purpose={sf.purpose}")
        return '[Normalised search params: ' + ', '.join(parts) + ']'


# ── AI Service Manager ────────────────────────────────────────────────────────

class AIServiceManager:
    """
    Orchestration layer between MessageRouter and RealTronAgent.

    Call process() instead of agent.chat() to get the full pipeline:
    guardrails → intent classification → direct route OR enriched LLM call.
    """

    def __init__(self):
        self._agent = None

    def _get_agent(self):
        if self._agent is None:
            from apps.ai.agent import get_agent
            self._agent = get_agent()
        return self._agent

    # ── Public interface ───────────────────────────────────────────────────────

    def process(
        self,
        phone:        str,
        message:      str,
        user=None,
        organization=None,
    ) -> str:
        """
        Process one inbound message for a specific org/lead context.
        Drop-in replacement for RealTronAgent.chat().
        """
        start = time.time()

        # ── 1. Guardrail: input check ──────────────────────────────────────────
        from apps.ai.guardrails import GuardrailEngine
        guard = GuardrailEngine.check_input(message)
        if not guard.safe:
            logger.info("Guardrail blocked: %s", guard.reason)
            return guard.fallback_reply

        # ── 2. Intent pre-classification (deterministic, no LLM) ──────────────
        agent   = self._get_agent()
        history = agent._load_history(phone, org=organization)
        intent  = IntentClassifier.classify(message, history)

        logger.debug(
            "Intent classified: %s confidence=%.2f lang=%s phone=%s",
            intent.intent, intent.confidence, intent.language, phone,
        )

        # ── 3. High-confidence direct route (pure Python, no LLM) ─────────────
        if intent.confidence >= _DIRECT_ROUTE_CONFIDENCE:
            direct_reply = self._try_direct_route(intent, phone, user, organization)
            if direct_reply:
                elapsed = time.time() - start
                ai_requests_total.labels(intent=intent.intent, route='direct').inc()
                ai_request_duration_seconds.labels(route='direct').observe(elapsed)
                agent._save_history(phone, organization, history, message, direct_reply)
                self._log_interaction(user, intent, message, direct_reply,
                                      int(elapsed * 1000), direct=True)
                self._record_wa_token(organization)
                return direct_reply

        # ── 4. Full LLM call with dynamic context injection ────────────────────
        from apps.ai.context import DynamicContextBuilder
        extra_context = DynamicContextBuilder.build(user, organization, phone)

        # Prepend normalised params so the LLM gets clean structured input
        enriched_message = message
        if intent.normalised_query and intent.intent == 'property_search':
            enriched_message = f"{intent.normalised_query}\n{message}"

        try:
            reply = agent.chat(
                phone, enriched_message, user,
                organization=organization,
                extra_context=extra_context,
            )
        except Exception as exc:
            logger.error("agent.chat failed phone=%s: %s", phone, exc, exc_info=True)
            reply = ''

        # ── 5. Output guardrail ────────────────────────────────────────────────
        if not GuardrailEngine.check_output(reply):
            logger.warning("LLM output failed guard — using fallback phone=%s", phone)
            reply = GuardrailEngine.fallback_error_reply(intent.language)

        elapsed = time.time() - start
        ai_requests_total.labels(intent=intent.intent, route='llm').inc()
        ai_request_duration_seconds.labels(route='llm').observe(elapsed)
        self._log_interaction(user, intent, message, reply,
                              int(elapsed * 1000), direct=False)
        self._record_wa_token(organization)
        return reply

    # ── Direct routing (no LLM) ────────────────────────────────────────────────

    def _try_direct_route(
        self,
        intent,
        phone:        str,
        user=None,
        organization=None,
    ) -> Optional[str]:
        """
        Attempt a direct Python tool call for deterministic intents.
        Returns formatted reply string, or None to fall through to LLM.
        """
        from apps.ai._tools_context import set_context
        set_context(user, phone, org=organization)

        org_country = getattr(organization, 'country', 'PK').upper() if organization else 'PK'
        _SUPPORTED_TAX_COUNTRIES = {'PK', 'AE', 'GB', 'US'}

        if intent.intent == 'scam_check' and intent.scam_input:
            return self._direct_scam_check(intent.scam_input, intent.language)

        if intent.intent == 'tax_advice' and intent.tax_input:
            if org_country in _SUPPORTED_TAX_COUNTRIES:
                intent.tax_input.country = org_country
                return self._direct_tax_advice(intent.tax_input, intent.language)
            return None     # unsupported market → LLM handles with general knowledge

        if intent.intent == 'loan_eligibility' and intent.loan_input:
            from apps.config.services import SystemConfigService
            if SystemConfigService.get_features().get('feature_loan_eligibility', True):
                intent.loan_input.country = org_country
                return self._direct_loan_eligibility(intent.loan_input, intent.language)
            return None

        if intent.intent == 'property_audit' and intent.audit_input:
            from apps.config.services import SystemConfigService
            if SystemConfigService.get_features().get('feature_property_audit', True):
                return self._direct_property_audit(intent.audit_input, intent.language)
            return None

        return None     # property_search and others go through LLM for natural formatting

    @staticmethod
    def _direct_scam_check(scam_input, language: str) -> str:
        if language and not language.startswith('en'):
            return None  # let LLM respond in user's language
        from apps.ai._tools_fraud import run_fraud_check
        result = run_fraud_check(**scam_input.to_tool_kwargs())
        if result.get('error'):
            return None

        risk  = result.get('risk', 'unknown').upper()
        score = result.get('risk_score', 0)
        flags = result.get('flags', [])
        rec   = result.get('recommendation', '')
        steps = result.get('verify_steps', [])

        emoji = {'HIGH': '🔴', 'MEDIUM': '🟡', 'LOW': '🟢'}.get(risk, '⚪')
        lines = [
            f"{emoji} *SCAM CHECK RESULT*",
            f"Risk Level: *{risk}* ({score}/100)",
            '',
        ]
        if flags:
            lines.append('⚠️ *Red Flags Detected:*')
            for f in flags[:5]:
                lines.append(f'  • {f}')
            lines.append('')
        if rec:
            lines.append(f'*Recommendation:* {rec}')
        if steps:
            lines.append('')
            lines.append('✅ *Verify these steps:*')
            for i, step in enumerate(steps[:4], 1):
                lines.append(f'{i}. {step}')
        lines += ['', '_Consult a registered property lawyer before any payment._']
        return '\n'.join(lines)

    @staticmethod
    def _direct_tax_advice(tax_input, language: str) -> str:
        if language and not language.startswith('en'):
            return None  # let LLM respond in user's language
        from apps.ai._tools_financial import calculate_7e_tax
        from apps.markets.registry import get_market_config
        result = calculate_7e_tax(**tax_input.to_tool_kwargs())
        if result.get('error'):
            return None

        cfg = get_market_config(tax_input.country)
        sym = cfg.currency
        fmv = tax_input.fmv_pkr

        lines = [
            '💰 *PROPERTY TAX SUMMARY*',
            f'Property Value: {sym} {fmv:,}',
        ]
        if tax_input.country == 'PK':
            lines.append(f'Filer Status: {tax_input.filer_status.replace("_", "-")}')
        lines.append('')

        if result.get('exempt'):
            lines.append('✅ *TAX EXEMPT*')
            if result.get('exemption_reason'):
                lines.append(f'   Reason: {result["exemption_reason"]}')
        else:
            annual = result.get('annual_tax', 0)
            if annual:
                label = 'Section 7E (annual)' if tax_input.country == 'PK' else 'Annual Property Tax'
                lines.append(f'*{label}:* {sym} {annual:,}/year')

            transfer = result.get('transfer_tax', 0)
            if transfer:
                label = 'Transfer Tax / DLD Fee' if tax_input.country == 'AE' else 'Transfer Tax'
                lines.append(f'*{label}:* {sym} {transfer:,}')

            stamp = result.get('stamp_duty', 0)
            if stamp:
                label = 'Stamp Duty (SDLT)' if tax_input.country == 'GB' else 'Stamp Duty'
                lines.append(f'*{label}:* {sym} {stamp:,}')

            wht = result.get('withholding_tax', 0)
            if wht:
                lines.append(f'*Withholding Tax:* {sym} {wht:,}')

        if result.get('advice'):
            lines += ['', result['advice']]

        for note in result.get('notes', [])[:2]:
            lines.append(f'ℹ️ {note}')

        lines += ['', '_Consult a registered professional or tax advisor for final advice._']
        return '\n'.join(lines)

    @staticmethod
    def _direct_loan_eligibility(loan_input, language: str) -> Optional[str]:
        if language and not language.startswith('en'):
            return None  # let LLM respond in user's language
        from apps.ai._tools_financial import check_loan_eligibility
        from apps.markets.registry import get_market_config
        result = check_loan_eligibility(**loan_input.to_tool_kwargs())
        if not result.get('supported', True) or result.get('error'):
            return None

        cfg = get_market_config(loan_input.country)
        sym = cfg.currency

        eligible = result.get('eligible', False)
        emi      = result.get('estimated_monthly_emi', 0)
        max_loan = result.get('max_affordable_loan', 0)
        rate     = result.get('annual_interest_rate_percent', 0)
        tenure   = result.get('tenure_years', loan_input.tenure_years)
        reason   = result.get('reason', '')
        steps    = result.get('next_steps', [])

        icon = '✅' if eligible else '❌'
        lines = [
            '🏦 *LOAN / MORTGAGE ELIGIBILITY*',
            f'Monthly Income: {sym} {loan_input.monthly_income:,}',
            f'Loan Requested: {sym} {loan_input.loan_amount:,}',
            '',
            f'{icon} *{"ELIGIBLE" if eligible else "NOT ELIGIBLE"}*',
        ]
        if eligible and emi:
            lines += [
                f'Estimated Monthly Payment: {sym} {emi:,}/month',
                f'Interest Rate: {rate}% p.a. over {tenure} years',
            ]
        if max_loan:
            lines += ['', f'Max Loan You Qualify For: {sym} {max_loan:,}']
        if reason:
            lines += ['', reason]
        if loan_input.country == 'PK' and loan_input.scheme == 'apna_ghar':
            lines += ['', '🏠 *Apna Ghar Scheme* — subsidized government financing']
        if steps:
            lines += ['', '*Next Steps:*']
            for i, step in enumerate(steps[:4], 1):
                lines.append(f'{i}. {step}')
        lines += ['', '_Consult your bank or a registered mortgage broker for final approval._']
        return '\n'.join(lines)

    @staticmethod
    def _direct_property_audit(audit_input, language: str) -> Optional[str]:
        if language and not language.startswith('en'):
            return None  # let LLM respond in user's language
        from apps.ai.tools import generate_property_audit
        result = generate_property_audit(**audit_input.to_tool_kwargs())
        if not result.get('success'):
            return None
        return result.get('whatsapp_summary')

    # ── Usage recording ────────────────────────────────────────────────────────

    @staticmethod
    def _record_wa_token(organization) -> None:
        if organization is None:
            return
        try:
            from apps.billing.ledger import UsageLedger
            UsageLedger.increment_wa_tokens(str(organization.id))
        except Exception:
            pass  # never block AI reply on billing failure

    # ── Logging ────────────────────────────────────────────────────────────────

    @staticmethod
    def _log_interaction(user, intent, message: str, reply: str,
                         response_ms: int, direct: bool):
        try:
            from apps.ai.models import AIInteraction
            AIInteraction.objects.create(
                user=user,
                interaction_type='intent_classify',
                model_used='direct_route' if direct else 'llm',
                response_ms=response_ms,
                input_data={
                    'message':    message[:300],
                    'intent':     intent.intent,
                    'confidence': intent.confidence,
                    'direct':     direct,
                },
                output_data={'reply': reply[:300]},
            )
        except Exception:
            pass


# ── Module-level singleton ─────────────────────────────────────────────────────

_service_instance: Optional[AIServiceManager] = None


def get_service_manager() -> AIServiceManager:
    global _service_instance
    if _service_instance is None:
        _service_instance = AIServiceManager()
    return _service_instance
