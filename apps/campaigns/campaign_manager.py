"""
Campaign orchestration layer.
MetaTierRateLimiter  — Redis token bucket per org
MetaTemplateFetcher  — Meta API template listing with cache
CampaignOrchestrator — recipient building, dispatch, progress
"""
import logging
import random
import time
from dataclasses import dataclass, field as dc_field
from typing import Optional

import redis as redis_lib
import requests
from django.conf import settings
from django.core.cache import cache as django_cache

logger = logging.getLogger(__name__)

# ── Meta tier constants ────────────────────────────────────────────────────────

TIER_LIMITS: dict[int, int] = {
    1:   1_000,
    2:  10_000,
    3: 100_000,
}

# Tokens-per-second refill rate per tier
_TIER_RATE: dict[int, float] = {
    1: 1_000  / 86_400,   # ~0.0116 /s
    2: 10_000 / 86_400,   # ~0.1157 /s
    3: 80.0,              # Meta hard API limit
}

# Burst capacity (max tokens in bucket) — allow short bursts up to 10 messages
_BURST: int = 10

# ── Lua script for atomic token bucket ────────────────────────────────────────

_ACQUIRE_LUA = """
local key      = KEYS[1]
local rate     = tonumber(ARGV[1])
local now      = tonumber(ARGV[2])
local capacity = tonumber(ARGV[3])

local data     = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens   = tonumber(data[1]) or capacity
local last_ref = tonumber(data[2]) or now

local elapsed = math.max(0, now - last_ref)
tokens = math.min(capacity, tokens + elapsed * rate)

if tokens >= 1.0 then
    tokens = tokens - 1.0
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
    redis.call('EXPIRE', key, 86400)
    return 1
else
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
    redis.call('EXPIRE', key, 86400)
    return 0
end
"""


class RateLimitExceeded(Exception):
    pass


class MetaRateLimitError(Exception):
    """Raised when Meta API returns HTTP 429."""
    pass


class MetaTierRateLimiter:
    """
    Redis token bucket rate limiter scoped per org.
    Uses an atomic Lua script — safe under concurrent Celery workers.
    """

    def __init__(self):
        self._redis = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
        self._script = self._redis.register_script(_ACQUIRE_LUA)

    def _key(self, org_id: str) -> str:
        return f"wa_tier_bucket:{org_id}"

    def acquire(self, org_id: str, tier: int = 1) -> bool:
        """Return True if a send token is available and consumed, False otherwise."""
        rate     = _TIER_RATE.get(tier, _TIER_RATE[1])
        now      = time.time()
        capacity = float(_BURST)
        result = self._script(keys=[self._key(org_id)], args=[rate, now, capacity])
        return bool(result)

    def get_backoff_seconds(self, retry_count: int) -> float:
        """Exponential backoff with ±10% jitter, capped at 300 s."""
        base   = 2 ** retry_count
        jitter = base * 0.1 * (random.random() * 2 - 1)
        return max(1.0, min(base + jitter, 300))


# ── MetaTemplateFetcher ───────────────────────────────────────────────────────

_TEMPLATE_CACHE_TTL = 600  # 10 minutes
_META_GRAPH_URL = "https://graph.facebook.com/v20.0"


@dataclass
class TemplateInfo:
    name:         str
    language:     str
    category:     str
    components:   list
    body_preview: str = ''

    @classmethod
    def from_meta(cls, raw: dict) -> 'TemplateInfo':
        body = ''
        for c in raw.get('components', []):
            if c.get('type') == 'BODY':
                body = c.get('text', '')[:200]
                break
        return cls(
            name=raw['name'],
            language=raw.get('language', 'en_US'),
            category=raw.get('category', ''),
            components=raw.get('components', []),
            body_preview=body,
        )


class MetaTemplateFetcher:
    """Fetches Meta-approved WhatsApp templates with a 10-minute Redis cache."""

    def _waba_id(self, org) -> str:
        from apps.config.services import SystemConfigService
        try:
            cfg = org.whatsapp_config
            if cfg.waba_id:
                return cfg.waba_id
        except Exception:
            pass
        return SystemConfigService.get('wa_waba_id', '')

    def _access_token(self, org) -> str:
        from apps.config.services import SystemConfigService
        try:
            cfg = org.whatsapp_config
            if cfg.is_active and cfg.access_token:
                return cfg.access_token
        except Exception:
            pass
        return SystemConfigService.get('wa_access_token', '')

    def list_templates(self, org) -> list:
        cache_key = f"wa_templates:{org.id}"
        cached = django_cache.get(cache_key)
        if cached is not None:
            return [TemplateInfo(**t) for t in cached]

        waba_id = self._waba_id(org)
        token   = self._access_token(org)
        if not waba_id or not token:
            logger.warning("MetaTemplateFetcher: no waba_id or token for org %s", org.id)
            return []

        try:
            r = requests.get(
                f"{_META_GRAPH_URL}/{waba_id}/message_templates",
                headers={'Authorization': f'Bearer {token}'},
                params={'limit': 250, 'fields': 'name,language,status,category,components'},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get('data', [])
        except Exception as exc:
            logger.error("MetaTemplateFetcher: API error for org %s: %s", org.id, exc)
            return []

        approved = [
            TemplateInfo.from_meta(t) for t in data
            if t.get('status') == 'APPROVED'
        ]
        django_cache.set(cache_key, [vars(t) for t in approved], _TEMPLATE_CACHE_TTL)
        return approved

    def get_template(self, org, name: str, language: str) -> Optional[TemplateInfo]:
        return next(
            (t for t in self.list_templates(org)
             if t.name == name and t.language == language),
            None,
        )


# ── CampaignOrchestrator ──────────────────────────────────────────────────────

from dataclasses import dataclass as _dc


@_dc
class ProgressSnapshot:
    total:        int
    sent:         int
    failed:       int
    pending:      int
    pct_complete: float


class CampaignOrchestrator:
    """Builds recipient lists, dispatches sends, and reports progress."""

    _INTENT_FILTERS = frozenset({'buy', 'sell', 'rent', 'invest'})
    _STATUS_FILTERS = frozenset({'new', 'warm', 'qualified', 'cold'})

    def build_recipients(self, campaign) -> int:
        from apps.leads.models import Lead
        from apps.campaigns.models import CampaignRecipient

        qs = Lead.objects.filter(
            organization=campaign.organization,
        ).select_related('user').exclude(user__phone='')

        af = campaign.audience_filter
        if af in self._STATUS_FILTERS:
            qs = qs.filter(status=af)
        elif af in self._INTENT_FILTERS:
            qs = qs.filter(intent=af)

        if campaign.budget_min is not None:
            qs = qs.filter(budget_max__gte=campaign.budget_min)
        if campaign.budget_max is not None:
            qs = qs.filter(budget_min__lte=campaign.budget_max)
        if campaign.area_interest:
            qs = qs.filter(city_interest__icontains=campaign.area_interest)

        objs = [
            CampaignRecipient(
                campaign=campaign,
                lead=lead,
                phone=lead.user.phone,
                delivery_status=CampaignRecipient.DeliveryStatus.PENDING,
            )
            for lead in qs
            if lead.user and lead.user.phone
        ]

        CampaignRecipient.objects.bulk_create(objs, ignore_conflicts=True)
        count = CampaignRecipient.objects.filter(campaign=campaign).count()
        campaign.recipient_count = count
        campaign.save(update_fields=['recipient_count'])
        return count

    def dispatch_one(self, recipient, wa_client, rate_limiter) -> None:
        """
        Send to one recipient.
        Raises RateLimitExceeded if the token bucket is empty.
        Raises MetaRateLimitError on HTTP 429 from Meta.
        """
        from apps.campaigns.models import CampaignRecipient
        from django.utils import timezone

        campaign = recipient.campaign
        tier     = campaign.messaging_tier

        if not rate_limiter.acquire(str(campaign.organization_id), tier):
            raise RateLimitExceeded(
                f"Token bucket empty for org {campaign.organization_id} tier {tier}"
            )

        try:
            if campaign.meta_template_name:
                resp = wa_client.send_template(
                    recipient.phone,
                    campaign.meta_template_name,
                    language=campaign.meta_template_language,
                    components=campaign.meta_template_components or [],
                )
            else:
                resp = wa_client.send_text(
                    recipient.phone,
                    campaign.message_template,
                    skip_window_check=True,
                )

            msg_id = ''
            try:
                msg_id = resp['messages'][0]['id']
            except (KeyError, IndexError, TypeError):
                pass

            recipient.delivery_status = CampaignRecipient.DeliveryStatus.SENT
            recipient.sent_at         = timezone.now()
            recipient.meta_message_id = msg_id
            recipient.save(update_fields=['delivery_status', 'sent_at', 'meta_message_id'])

        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                raise MetaRateLimitError("Meta returned 429") from exc
            recipient.delivery_status = CampaignRecipient.DeliveryStatus.FAILED
            recipient.error_message   = str(exc)[:500]
            recipient.save(update_fields=['delivery_status', 'error_message'])

        except Exception as exc:
            recipient.delivery_status = CampaignRecipient.DeliveryStatus.FAILED
            recipient.error_message   = str(exc)[:500]
            recipient.save(update_fields=['delivery_status', 'error_message'])

    def get_progress(self, campaign_id: str) -> dict:
        from apps.campaigns.models import CampaignRecipient
        from django.db.models import Count, Case, When, IntegerField

        agg = CampaignRecipient.objects.filter(
            campaign_id=campaign_id,
        ).aggregate(
            total   = Count('id'),
            sent    = Count(Case(When(delivery_status='sent',    then=1), output_field=IntegerField())),
            failed  = Count(Case(When(delivery_status='failed',  then=1), output_field=IntegerField())),
            pending = Count(Case(When(delivery_status='pending', then=1), output_field=IntegerField())),
        )

        total = agg['total'] or 0
        sent  = agg['sent']  or 0
        pct   = round((sent + (agg['failed'] or 0)) / total * 100, 1) if total else 0.0

        return {
            'total':        total,
            'sent':         sent,
            'failed':       agg['failed']  or 0,
            'pending':      agg['pending'] or 0,
            'pct_complete': pct,
        }
