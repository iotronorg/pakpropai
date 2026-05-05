import logging
from django.core.cache import cache

from services.ai_orchestrator import AIOrchestrator

logger = logging.getLogger(__name__)


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
            ai_result = AIOrchestrator.fraud_check(query, user=user)
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