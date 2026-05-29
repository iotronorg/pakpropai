"""
ConversationScanner — finds high-quality WhatsApp conversations that ended in
HANDOVER or DEAL_LOCK outcomes and scores them for instruction-tuning suitability.
Read-only: never mutates any CRM data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CandidateConversation:
    session_id:   str
    messages:     list[dict]          # [{'role': 'user'|'assistant', 'content': str}]
    outcome_type: str                 # 'handover' | 'deal_lock'
    quality_score: float              # 0.0–1.0
    org_id:       str


class ConversationScanner:
    """
    Async-compatible (but synchronous internally) scanner.
    Returns a list of CandidateConversation scored for quality.
    """

    def __init__(self, min_quality: float = 0.7, limit: int = 5000):
        self.min_quality = min_quality
        self.limit       = limit

    def scan(self) -> list[CandidateConversation]:
        candidates: list[CandidateConversation] = []
        candidates.extend(self._scan_handover_sessions())
        candidates.extend(self._scan_deal_lock_sessions())
        # Deduplicate by session_id (a session could match both)
        seen: set[str] = set()
        unique = []
        for c in candidates:
            if c.session_id not in seen:
                seen.add(c.session_id)
                unique.append(c)
        return [c for c in unique if c.quality_score >= self.min_quality][: self.limit]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _scan_handover_sessions(self) -> list[CandidateConversation]:
        from apps.whatsapp.models import WhatsAppSession
        from apps.leads.models import LeadActivity

        results = []
        # Sessions belonging to leads that had a HANDOVER activity
        handover_lead_ids = (
            LeadActivity.objects
            .filter(action='handover')
            .values_list('lead__user__phone', flat=True)
            .distinct()
        )
        sessions = (
            WhatsAppSession.objects
            .filter(phone__in=handover_lead_ids)
            .select_related('organization')
            .prefetch_related('messages')
            [:self.limit * 2]
        )
        for session in sessions:
            msgs = self._extract_messages(session)
            if not msgs:
                continue
            score = self._quality_score(session, msgs)
            results.append(CandidateConversation(
                session_id=str(session.id),
                messages=msgs,
                outcome_type='handover',
                quality_score=score,
                org_id=str(session.organization_id) if session.organization_id else '',
            ))
        return results

    def _scan_deal_lock_sessions(self) -> list[CandidateConversation]:
        from apps.whatsapp.models import WhatsAppSession
        from apps.escrow.models import EscrowDeal

        results = []
        # Sessions belonging to users whose leads have a locked deal
        locked_phones = (
            EscrowDeal.objects
            .filter(status='locked')
            .values_list('lead__user__phone', flat=True)
            .distinct()
        )
        sessions = (
            WhatsAppSession.objects
            .filter(phone__in=locked_phones)
            .select_related('organization')
            .prefetch_related('messages')
            [:self.limit * 2]
        )
        for session in sessions:
            msgs = self._extract_messages(session)
            if not msgs:
                continue
            score = self._quality_score(session, msgs)
            results.append(CandidateConversation(
                session_id=str(session.id),
                messages=msgs,
                outcome_type='deal_lock',
                quality_score=score,
                org_id=str(session.organization_id) if session.organization_id else '',
            ))
        return results

    @staticmethod
    def _extract_messages(session) -> list[dict]:
        msgs = []
        for msg in session.messages.order_by('created_at'):
            body = (msg.body or '').strip()
            if not body:
                continue
            role = 'user' if msg.direction == 'inbound' else 'assistant'
            msgs.append({'role': role, 'content': body})
        return msgs

    @staticmethod
    def _quality_score(session, msgs: list[dict]) -> float:
        """
        Score 0.0–1.0 based on:
        - Outcome reached within first 10 turns: +0.4
        - Session was AI_MANAGED throughout: +0.3
        - At least 2 turns (not trivially short): +0.3
        """
        score = 0.0
        turn_count = len(msgs)

        if turn_count >= 2:
            score += 0.3
        if turn_count <= 10:
            score += 0.4
        elif turn_count <= 20:
            score += 0.2
        # Check conversation mode history (approximation: still AI_MANAGED at query time)
        if session.conversation_mode == 'AI_MANAGED':
            score += 0.3

        return min(1.0, score)
