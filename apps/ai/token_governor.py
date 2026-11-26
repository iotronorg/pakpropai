"""
Token governance layer — sliding-window budgeting, billing alerts.
(The embedding-similarity response cache was removed in session 31, KI-087; see apps/ai/faq.py.)

All Redis operations are fail-open: any exception logs WARNING and returns a safe
default so the WhatsApp response path is never blocked.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

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
                user=org.owner,
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
