import logging
from datetime import datetime

from django.core.cache import cache

from .limits import PLAN_LIMITS, DIMENSION_KEY_MAP

logger = logging.getLogger(__name__)

_AGENT_TTL     = None   # permanent
_INVENTORY_TTL = None   # permanent
_WA_TOKEN_TTL  = 60 * 60 * 24 * 35  # 35 days


def _key_agents(org_id: str) -> str:
    return f'billing:{org_id}:agents'


def _key_inventory(org_id: str) -> str:
    return f'billing:{org_id}:inventory'


def _key_wa_tokens(org_id: str, period: str | None = None) -> str:
    if period is None:
        period = datetime.utcnow().strftime('%Y-%m')
    return f'billing:{org_id}:wa_tokens:{period}'


class UsageLedger:

    # ── Agents ─────────────────────────────────────────────────────────────────

    @classmethod
    def get_agent_count(cls, org_id: str) -> int:
        return int(cache.get(_key_agents(str(org_id))) or 0)

    @classmethod
    def increment_agents(cls, org_id: str, n: int = 1) -> None:
        key = _key_agents(str(org_id))
        try:
            cache.add(key, 0)
            cache.incr(key, n)
        except Exception:
            logger.warning('UsageLedger.increment_agents failed org=%s', org_id)

    @classmethod
    def decrement_agents(cls, org_id: str, n: int = 1) -> None:
        key = _key_agents(str(org_id))
        try:
            current = int(cache.get(key) or 0)
            new_val = max(0, current - n)
            cache.set(key, new_val)
        except Exception:
            logger.warning('UsageLedger.decrement_agents failed org=%s', org_id)

    # ── Inventory ──────────────────────────────────────────────────────────────

    @classmethod
    def get_inventory_count(cls, org_id: str) -> int:
        return int(cache.get(_key_inventory(str(org_id))) or 0)

    @classmethod
    def increment_inventory(cls, org_id: str, n: int = 1) -> None:
        key = _key_inventory(str(org_id))
        try:
            cache.add(key, 0)
            cache.incr(key, n)
        except Exception:
            logger.warning('UsageLedger.increment_inventory failed org=%s', org_id)

    @classmethod
    def decrement_inventory(cls, org_id: str, n: int = 1) -> None:
        key = _key_inventory(str(org_id))
        try:
            current = int(cache.get(key) or 0)
            new_val = max(0, current - n)
            cache.set(key, new_val)
        except Exception:
            logger.warning('UsageLedger.decrement_inventory failed org=%s', org_id)

    # ── WhatsApp AI turns ──────────────────────────────────────────────────────

    @classmethod
    def get_wa_token_count(cls, org_id: str, period: str | None = None) -> int:
        return int(cache.get(_key_wa_tokens(str(org_id), period)) or 0)

    @classmethod
    def increment_wa_tokens(cls, org_id: str, n: int = 1) -> None:
        key = _key_wa_tokens(str(org_id))
        try:
            if not cache.add(key, n, _WA_TOKEN_TTL):
                cache.incr(key, n)
        except Exception:
            logger.warning('UsageLedger.increment_wa_tokens failed org=%s', org_id)

    # ── Limit check ────────────────────────────────────────────────────────────

    @classmethod
    def within_limit(cls, org_id: str, plan: str, dimension: str) -> bool:
        """
        Returns True if current usage is within the plan limit for this dimension.
        Always returns True for enterprise (but still usable for recording).
        Fails open if Redis is unavailable.
        """
        try:
            plan_cfg  = PLAN_LIMITS.get(plan, PLAN_LIMITS['trial'])
            limit_key = DIMENSION_KEY_MAP.get(dimension)
            if limit_key is None:
                logger.error('within_limit: unknown dimension %s', dimension)
                return True

            max_val = plan_cfg.get(limit_key)
            if max_val is None:
                return True  # unlimited (enterprise)

            if dimension == 'agents':
                current = cls.get_agent_count(org_id)
            elif dimension == 'inventory':
                current = cls.get_inventory_count(org_id)
            elif dimension == 'wa_tokens':
                current = cls.get_wa_token_count(org_id)
            else:
                return True

            return current < max_val

        except Exception:
            logger.critical(
                'UsageLedger.within_limit: Redis unavailable — failing open org=%s plan=%s dim=%s',
                org_id, plan, dimension,
            )
            return True  # fail open: uptime > revenue protection

    # ── Admin / test utility ───────────────────────────────────────────────────

    @classmethod
    def reset_org_counters(cls, org_id: str) -> None:
        period = datetime.utcnow().strftime('%Y-%m')
        keys = [
            _key_agents(str(org_id)),
            _key_inventory(str(org_id)),
            _key_wa_tokens(str(org_id), period),
        ]
        cache.delete_many(keys)
