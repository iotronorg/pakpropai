"""
Token governance layer — semantic caching, sliding-window budgeting, billing alerts.

All Redis operations are fail-open: any exception logs WARNING and returns a safe
default so the WhatsApp response path is never blocked.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import struct
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL = 86400        # 24 h
_BUDGET_WINDOW = 86400    # 24 h
_THROTTLE_TTL = 300       # 5 min

# Plan → daily token limit (fallback when SystemConfig is unavailable)
_PLAN_LIMITS: dict[str, int] = {
    'trial':        50_000,
    'basic':       200_000,
    'professional': 1_000_000,
    'enterprise':   5_000_000,
}


# ── Redis helper ──────────────────────────────────────────────────────────────

def _r():
    from django.core.cache import caches
    try:
        return caches['default'].client.get_client()
    except Exception:
        import redis as _rlib
        from django.conf import settings
        return _rlib.from_url(getattr(settings, 'CELERY_BROKER_URL', 'redis://localhost:6379/0'))


# ── Embedding model (singleton) ───────────────────────────────────────────────

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
    return _model


def _embed(text: str):
    """Return float32 ndarray of shape (384,)."""
    import numpy as np
    return _get_model().encode(text, normalize_embeddings=True).astype(np.float32)


def _cosine_sim(a, b) -> float:
    """Cosine similarity of two normalised vectors (dot product suffices)."""
    import numpy as np
    return float(np.dot(a, b))


def _vec_to_b64(vec) -> str:
    return base64.b64encode(vec.tobytes()).decode()


def _b64_to_vec(s: str):
    import numpy as np
    raw = base64.b64decode(s)
    return np.frombuffer(raw, dtype=np.float32)


# ── SemanticResponseCache ─────────────────────────────────────────────────────

class SemanticResponseCache:
    """
    Org-isolated semantic cache backed by Redis HSET.
    Keys: sem_cache:{org_id}:{sha256(text)[:12]}
    Fields: embedding_b64, response, created_at
    """

    @staticmethod
    def lookup(text: str, org_id: str, threshold: float = 0.96) -> Optional[str]:
        if not text or not org_id:
            return None
        try:
            r = _r()
            query_vec = _embed(text)
            pattern = f'sem_cache:{org_id}:*'
            best_sim = 0.0
            best_response = None

            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=pattern, count=100)
                for key in keys:
                    entry = r.hgetall(key)
                    if not entry:
                        continue
                    emb_b64 = entry.get(b'embedding_b64', entry.get('embedding_b64', b''))
                    if isinstance(emb_b64, bytes):
                        emb_b64 = emb_b64.decode()
                    if not emb_b64:
                        continue
                    stored_vec = _b64_to_vec(emb_b64)
                    sim = _cosine_sim(query_vec, stored_vec)
                    if sim > best_sim:
                        best_sim = sim
                        resp = entry.get(b'response', entry.get('response', b''))
                        best_response = resp.decode() if isinstance(resp, bytes) else resp
                if cursor == 0:
                    break

            if best_sim >= threshold and best_response:
                logger.debug("SemanticCache HIT org=%s sim=%.4f", org_id, best_sim)
                return best_response
            return None
        except Exception:
            logger.warning('SemanticResponseCache.lookup failed', exc_info=True)
            return None

    @staticmethod
    def store(text: str, org_id: str, response: str) -> None:
        if not text or not org_id:
            return
        try:
            r = _r()
            sha_key = hashlib.sha256(text.encode()).hexdigest()[:12]
            key = f'sem_cache:{org_id}:{sha_key}'
            vec = _embed(text)
            r.hset(key, mapping={
                'embedding_b64': _vec_to_b64(vec),
                'response': response,
                'created_at': int(time.time()),
            })
            r.expire(key, _CACHE_TTL)
        except Exception:
            logger.warning('SemanticResponseCache.store failed', exc_info=True)


# ── BudgetStatus ──────────────────────────────────────────────────────────────

@dataclass
class BudgetStatus:
    used: int
    limit: int
    percent: float
    state: str  # ok | warning | throttled | hard_limit


# ── SlidingWindowTokenBudget ──────────────────────────────────────────────────

class SlidingWindowTokenBudget:

    @staticmethod
    def record_tokens(org_id: str, tokens_in: int, tokens_out: int) -> None:
        try:
            r = _r()
            key = f'token_budget:{org_id}'
            total = tokens_in + tokens_out
            now = time.time()
            r.zadd(key, {f'{now}:{total}': now})
            r.zremrangebyscore(key, '-inf', now - _BUDGET_WINDOW)
            r.expire(key, _BUDGET_WINDOW + 60)
        except Exception:
            logger.warning('SlidingWindowTokenBudget.record_tokens failed', exc_info=True)

    @staticmethod
    def get_spend_24h(org_id: str) -> int:
        try:
            r = _r()
            key = f'token_budget:{org_id}'
            now = time.time()
            entries = r.zrangebyscore(key, now - _BUDGET_WINDOW, '+inf', withscores=False)
            total = 0
            for entry in entries:
                raw = entry.decode() if isinstance(entry, bytes) else entry
                parts = raw.split(':', 1)
                if len(parts) == 2:
                    try:
                        total += int(parts[1])
                    except ValueError:
                        pass
            return total
        except Exception:
            logger.warning('SlidingWindowTokenBudget.get_spend_24h failed', exc_info=True)
            return 0

    @staticmethod
    def get_tier_limit(org) -> int:
        try:
            from apps.config.services import SystemConfigService
            plan = getattr(org, 'plan', 'trial')
            key = f'token_limit_{plan}'
            val = SystemConfigService.get(key)
            if val:
                return int(val)
        except Exception:
            pass
        plan = getattr(org, 'plan', 'trial')
        return _PLAN_LIMITS.get(plan, _PLAN_LIMITS['trial'])

    @classmethod
    def check_budget(cls, org_id: str, org) -> BudgetStatus:
        used = cls.get_spend_24h(org_id)
        limit = cls.get_tier_limit(org)
        percent = (used / limit * 100) if limit > 0 else 0.0

        if percent >= 100:
            state = 'hard_limit'
        elif percent >= 95:
            state = 'throttled'
        elif percent >= 80:
            state = 'warning'
        else:
            state = 'ok'

        return BudgetStatus(used=used, limit=limit, percent=round(percent, 2), state=state)


# ── BillingAlertService ───────────────────────────────────────────────────────

class BillingAlertService:

    @staticmethod
    def trigger_budget_alert(org, status: BudgetStatus) -> None:
        try:
            r = _r()
            dedup_key = f'budget_alert:{org.id}:{status.state}'
            if r.set(dedup_key, '1', ex=3600, nx=True) is None:
                return  # already alerted within 1 h

            from apps.notifications.models import Notification
            Notification.objects.create(
                user=org.admin_user,
                title='Token budget alert',
                message=(
                    f'Your AI token budget is at {status.percent:.0f}% '
                    f'({status.used:,}/{status.limit:,} tokens). '
                    f'State: {status.state}.'
                ),
            )
        except Exception:
            logger.warning('BillingAlertService.trigger_budget_alert failed', exc_info=True)

    @staticmethod
    def trigger_throttle_state(org) -> None:
        try:
            r = _r()
            r.set(f'ai_throttle:{org.id}', '1', ex=_THROTTLE_TTL)
        except Exception:
            logger.warning('BillingAlertService.trigger_throttle_state failed', exc_info=True)

    @staticmethod
    def clear_throttle(org_id: str) -> None:
        try:
            r = _r()
            r.delete(f'ai_throttle:{org_id}')
        except Exception:
            logger.warning('BillingAlertService.clear_throttle failed', exc_info=True)

    @staticmethod
    def is_throttled(org_id: str) -> bool:
        try:
            r = _r()
            return bool(r.exists(f'ai_throttle:{org_id}'))
        except Exception:
            logger.warning('BillingAlertService.is_throttled failed', exc_info=True)
            return False
