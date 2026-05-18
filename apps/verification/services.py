import logging
from django.core.cache import cache
from django.utils import timezone

from apps.ai.scoring import fraud_check as _ai_fraud_check

logger = logging.getLogger(__name__)


class VerificationSignalService:
    """Computes a 0-100 integrity score from all available signals on a Verification."""

    # Document types that carry the most legal weight in Pakistan
    CORE_DOC_TYPES = {'fard', 'sale_deed', 'allotment'}

    @classmethod
    def compute_score(cls, verification) -> int:
        scans = list(verification.document_scans.all())
        score = 40  # neutral baseline

        # --- document signals ---
        for scan in scans:
            if scan.confidence == 'HIGH':
                score += 12
            elif scan.confidence == 'MEDIUM':
                score += 6
            else:
                score += 2

            # deduct for each red flag found in this scan
            score -= len(scan.red_flags) * 15

        # bonus: has at least one core legal document
        submitted_types = {s.document_type for s in scans}
        if submitted_types & cls.CORE_DOC_TYPES:
            score += 15

        # bonus: multiple document types (completeness)
        if len(submitted_types) >= 3:
            score += 10

        # --- verification-level fraud flags ---
        score -= len(verification.fraud_flags) * 20

        # bonus: no flags anywhere
        all_red_flags = sum(len(s.red_flags) for s in scans)
        if all_red_flags == 0 and not verification.fraud_flags and scans:
            score += 10

        return max(0, min(100, score))

    @classmethod
    def refresh(cls, verification) -> int:
        score = cls.compute_score(verification)
        verification.signal_score = score
        verification.save(update_fields=['signal_score'])
        return score


class FraudCheckService:

    BLACKLIST_KEY = 'fraud:blacklist:'

    @classmethod
    def check(cls, query: str, user=None) -> dict:
        # 1. Quick blacklist lookup (fast path, no AI cost)
        for token in query.lower().split():
            if cache.get(f"{cls.BLACKLIST_KEY}{token}"):
                return {
                    'risk': 'high',
                    'flags': [f'"{token}" is on the known fraud blacklist.'],
                    'recommendation': 'Avoid this transaction immediately.',
                    'verify_steps': ['Report to FBR Property Verification Portal',
                                     'File complaint with local police'],
                    'source': 'blacklist',
                }

        # 2. AI-powered analysis
        try:
            ai_result = _ai_fraud_check(query)
        except Exception as exc:
            logger.error(f"Fraud AI failed: {exc}")
            return {
                'risk': 'unknown',
                'flags': ['AI service temporarily unavailable.'],
                'recommendation': 'Try again in a few minutes.',
                'verify_steps': [],
                'source': 'fallback',
            }

        ai_result['source'] = 'ai'
        return ai_result

    @classmethod
    def add_to_blacklist(cls, token: str, ttl_days: int = 7):
        cache.set(f"{cls.BLACKLIST_KEY}{token.lower()}", True, ttl_days * 86400)