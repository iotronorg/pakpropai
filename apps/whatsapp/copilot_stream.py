"""
AI Co-Pilot streaming — intent extraction and recommendation building for live agent sessions.

CopilotIntentExtractor  — lightweight keyword + IntentClassifier-based intent detection
CopilotRecommendationEngine — builds up to 4 recommendation types per inbound message
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Keywords that trigger legal_flag=True
_LEGAL_KEYWORDS = frozenset([
    'title deed', 'noc', 'no objection', 'freehold', 'leasehold',
    'ownership document', 'registry', 'allotment letter', 'possession letter',
    'transfer', 'legal', 'court', 'dispute', 'mortgage', 'encumbrance',
])

# Keywords that trigger tax sheet recommendation
_TAX_KEYWORDS = frozenset([
    'tax', 'cgt', 'wht', 'capital gains', 'withholding', 'fbr',
    'stamp duty', 'transfer fee',
])

# Bedroom extraction pattern (e.g. "3 bed", "2-bedroom", "4BHK")
_BED_RE = re.compile(r'\b(\d)\s*(?:bed|br|bedroom|bhk)\b', re.IGNORECASE)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class CopilotIntent:
    intent:      str
    keywords:    list[str] = field(default_factory=list)
    unit_config: Optional[str] = None   # e.g. "3bed"
    legal_flag:  bool = False
    confidence:  float = 0.0


@dataclass
class RecommendationItem:
    type:    str    # 'inventory_card' | 'doc_link' | 'tax_sheet' | 'ai_response'
    payload: dict


# ── CopilotIntentExtractor ────────────────────────────────────────────────────

class CopilotIntentExtractor:
    """
    Lightweight intent extraction for copilot recommendations.
    Uses keyword matching + the existing IntentClassifier (no extra LLM call).
    Fail-open: returns unknown intent on any exception.
    """

    @staticmethod
    def extract(message_text: str, session_context: dict | None = None) -> CopilotIntent:
        try:
            text_lower = message_text.lower()

            # Legal flag
            legal_flag = any(kw in text_lower for kw in _LEGAL_KEYWORDS)

            # Keyword list
            found_keywords = [kw for kw in _TAX_KEYWORDS if kw in text_lower]

            # Bedroom extraction
            bed_match = _BED_RE.search(message_text)
            unit_config = f"{bed_match.group(1)}bed" if bed_match else None

            # Intent via existing classifier
            from apps.ai.service import IntentClassifier
            history = (session_context or {}).get('history', [])
            result = IntentClassifier.classify(message_text, history)

            return CopilotIntent(
                intent=result.intent,
                keywords=found_keywords,
                unit_config=unit_config,
                legal_flag=legal_flag,
                confidence=result.confidence,
            )
        except Exception:
            logger.warning('CopilotIntentExtractor.extract failed', exc_info=True)
            return CopilotIntent(intent='unknown', confidence=0.0)


# ── CopilotRecommendationEngine ───────────────────────────────────────────────

class CopilotRecommendationEngine:
    """
    Builds a list of RecommendationItems from a CopilotIntent.
    All four sources are fail-open — a single source failure returns [] for that source.
    """

    @staticmethod
    def build_recommendations(
        intent: CopilotIntent,
        session,
        org,
    ) -> list[RecommendationItem]:
        results: list[RecommendationItem] = []

        # ── Source 1: Inventory cards ─────────────────────────────────────────
        try:
            results.extend(CopilotRecommendationEngine._inventory_cards(intent, org))
        except Exception:
            logger.warning('CopilotRecommendationEngine: inventory_cards failed', exc_info=True)

        # ── Source 2: Tax sheet ───────────────────────────────────────────────
        try:
            if set(intent.keywords) & _TAX_KEYWORDS:
                tax = CopilotRecommendationEngine._tax_sheet(session, org)
                if tax:
                    results.append(tax)
        except Exception:
            logger.warning('CopilotRecommendationEngine: tax_sheet failed', exc_info=True)

        # ── Source 3: AI response suggestion ─────────────────────────────────
        try:
            ai_item = CopilotRecommendationEngine._ai_response(intent, session, org)
            if ai_item:
                results.append(ai_item)
        except Exception:
            logger.warning('CopilotRecommendationEngine: ai_response failed', exc_info=True)

        # ── Source 4: Doc links (legal) ───────────────────────────────────────
        try:
            if intent.legal_flag:
                results.extend(CopilotRecommendationEngine._doc_links(session))
        except Exception:
            logger.warning('CopilotRecommendationEngine: doc_links failed', exc_info=True)

        return results

    # ── Private sources ───────────────────────────────────────────────────────

    @staticmethod
    def _inventory_cards(intent: CopilotIntent, org) -> list[RecommendationItem]:
        from apps.properties.models import Property
        qs = (
            Property.objects
            .filter(organization=org, is_active=True)
            .order_by('-ai_score')[:3]
        )
        items = []
        for prop in qs:
            thumbnail_url = None
            try:
                img = prop.images.filter(is_primary=True).first()
                if img and img.image:
                    thumbnail_url = img.image.url
            except Exception:
                pass
            items.append(RecommendationItem(
                type='inventory_card',
                payload={
                    'property_id':   str(prop.id),
                    'title':         prop.title,
                    'bedrooms':      None,
                    'price':         prop.price,
                    'currency':      prop.currency,
                    'area_sqm':      float(prop.area_sqm) if prop.area_sqm else None,
                    'thumbnail_url': thumbnail_url,
                },
            ))
        return items

    @staticmethod
    def _tax_sheet(session, org) -> Optional[RecommendationItem]:
        from apps.leads.models import Lead
        lead = Lead.objects.filter(
            phone=session.phone,
            organization=org,
        ).order_by('-created_at').first()
        budget = lead.budget if lead else None

        try:
            from apps.markets.registry import get_tax_calculator
            calc = get_tax_calculator(org.country)
            if not calc.supported:
                return None
            result = calc.calculate_withholding_tax(budget or 0, filer_status='filer')
            return RecommendationItem(
                type='tax_sheet',
                payload={
                    'wht_amount':    result.wht_amount,
                    'cgt_amount':    getattr(result, 'cgt_amount', 0),
                    'effective_rate': result.effective_rate,
                    'breakdown':     result.breakdown,
                },
            )
        except Exception:
            return None

    @staticmethod
    def _ai_response(intent: CopilotIntent, session, org) -> Optional[RecommendationItem]:
        try:
            from apps.ai.service import get_service_manager
            svc = get_service_manager()
            prompt = (
                f"[COPILOT] Suggest a brief agent reply (max 2 sentences) for intent='{intent.intent}'. "
                f"Be professional and helpful."
            )
            suggested = svc.process(
                phone=session.phone,
                message=prompt,
                organization=org,
            )
            if suggested:
                return RecommendationItem(
                    type='ai_response',
                    payload={'suggested_reply': suggested, 'confidence': intent.confidence},
                )
        except Exception:
            logger.warning('CopilotRecommendationEngine._ai_response failed', exc_info=True)
        return None

    @staticmethod
    def _doc_links(session) -> list[RecommendationItem]:
        items = []
        try:
            from apps.verification.models import DocumentScan
            scans = DocumentScan.objects.filter(
                verification__lead__phone=session.phone,
            ).select_related('verification')[:3]
            for scan in scans:
                items.append(RecommendationItem(
                    type='doc_link',
                    payload={
                        'document_id': str(scan.id),
                        'title':       getattr(scan, 'document_type', 'Document'),
                        'url':         getattr(scan, 'file_url', ''),
                        'doc_type':    getattr(scan, 'document_type', 'unknown'),
                    },
                ))
        except Exception:
            logger.warning('CopilotRecommendationEngine._doc_links failed', exc_info=True)
        return items
