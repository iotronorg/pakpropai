"""
BI analytics computation layer.

All functions are org-scoped and cache-friendly. Redis sorted sets power
the 24-hour agent speed leaderboard. Funnel and token usage are cached
for 5 minutes via the Django cache backend.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

_FUNNEL_TTL      = 60 * 5
_WA_TOKEN_TTL    = 60 * 5
_LEADERBOARD_TTL = 60 * 60 * 24
_LB_KEY          = 'analytics:{org_id}:leaderboard:spd'


# ── internal helpers ───────────────────────────────────────────────────────────

def _redis():
    import redis as redis_lib
    return redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _pct(a, b):
    return round(a / b * 100, 1) if b else 0.0


# ── 6-stage funnel ─────────────────────────────────────────────────────────────

def compute_funnel(org):
    """
    Return the 6-stage sales funnel for org.

    Stages map to the lead state machine:
      Discover  – all org leads
      Qualify   – status warm or qualified
      Verify    – left AI queue (org-scoped)
      Connect   – agent assigned or closed
      Negotiate – status qualified
      Transact  – closed routing + locked/released deal
    """
    cache_key = f'analytics:{org.id}:funnel'
    hit = cache.get(cache_key)
    if hit is not None:
        return hit

    from apps.leads.models import Lead
    from apps.escrow.models import EscrowDeal

    qs = Lead.objects.filter(organization=org)

    discover  = qs.count()
    qualify   = qs.filter(status__in=['warm', 'qualified']).count()
    verify    = qs.exclude(routing_state='ai_queue').count()
    connect   = qs.filter(routing_state__in=['agent_assigned', 'closed']).count()
    negotiate = qs.filter(status='qualified').count()
    transact  = EscrowDeal.objects.filter(
        property__organization=org,
        status__in=['locked', 'released'],
    ).count()

    stages = [
        {'stage': 'Discover',  'count': discover,  'conversion': 100.0},
        {'stage': 'Qualify',   'count': qualify,   'conversion': _pct(qualify,   discover)},
        {'stage': 'Verify',    'count': verify,    'conversion': _pct(verify,    discover)},
        {'stage': 'Connect',   'count': connect,   'conversion': _pct(connect,   discover)},
        {'stage': 'Negotiate', 'count': negotiate, 'conversion': _pct(negotiate, discover)},
        {'stage': 'Transact',  'count': transact,  'conversion': _pct(transact,  discover)},
    ]

    result = {
        'stages': stages,
        'total_leads': discover,
        'overall_conversion': _pct(transact, discover),
    }
    cache.set(cache_key, result, _FUNNEL_TTL)
    return result


# ── WhatsApp token usage ───────────────────────────────────────────────────────

def compute_wa_token_usage(org):
    """
    Return last 6 months of WA token usage from Redis billing keys.
    Key pattern: billing:{org_id}:wa_tokens:{YYYY-MM}
    """
    cache_key = f'analytics:{org.id}:wa_tokens_agg'
    hit = cache.get(cache_key)
    if hit is not None:
        return hit

    from datetime import timedelta

    now = timezone.now()
    months = []
    total = 0

    for i in range(5, -1, -1):
        dt = now - timedelta(days=30 * i)
        period = dt.strftime('%Y-%m')
        redis_key = f'billing:{org.id}:wa_tokens:{period}'
        count = int(cache.get(redis_key) or 0)
        months.append({'period': period, 'tokens': count})
        total += count

    result = {
        'monthly': months,
        'current_period': now.strftime('%Y-%m'),
        'current_month': months[-1]['tokens'],
        'total_6m': total,
    }
    cache.set(cache_key, result, _WA_TOKEN_TTL)
    return result


# ── Agent speed leaderboard ────────────────────────────────────────────────────

def _rebuild_leaderboard(org):
    """Compute leaderboard from DB and write to Redis sorted set."""
    from apps.agents.stats import compute_leaderboard
    from apps.agents.models import Agent

    rows = compute_leaderboard(
        Agent.objects.filter(organization=org, is_active=True)
    )

    key = _LB_KEY.format(org_id=org.id)
    try:
        r = _redis()
        mapping = {}
        for row in rows:
            score = row['avg_response_time_hours']
            mapping[str(row['agent_id'])] = score if score is not None else 9999.0
        if mapping:
            r.zadd(key, mapping)
            r.expire(key, _LEADERBOARD_TTL)
    except Exception:
        logger.warning('leaderboard ZADD failed org=%s', org.id)

    return rows


def get_agent_speed_leaderboard(org, top_n: int = 10):
    """
    Return top_n agents ranked by ascending response time (fastest first).
    Reads from Redis sorted set; falls back to DB if the key is absent.
    """
    key = _LB_KEY.format(org_id=org.id)
    try:
        r = _redis()
        entries = r.zrange(key, 0, top_n - 1, withscores=True)
        if entries:
            from apps.agents.models import Agent
            id_to_score = {int(aid): scr for aid, scr in entries}
            agents = list(
                Agent.objects.filter(
                    id__in=id_to_score.keys(), is_active=True
                ).select_related('user')
            )
            agents.sort(key=lambda a: id_to_score.get(a.id, 9999.0))
            rows = []
            for i, agent in enumerate(agents):
                scr = id_to_score.get(agent.id)
                rows.append({
                    'rank': i + 1,
                    'agent_id': agent.id,
                    'name': agent.name,
                    'avg_response_time_hours': None if scr == 9999.0 else scr,
                    'closed_deals': agent.closed_deals,
                    'rating': float(agent.rating),
                })
            return rows
    except Exception:
        logger.warning('leaderboard ZRANGE failed org=%s — rebuilding', org.id)

    rows = _rebuild_leaderboard(org)
    return [{
        'rank': r['rank'],
        'agent_id': r['agent_id'],
        'name': r['name'],
        'avg_response_time_hours': r['avg_response_time_hours'],
        'closed_deals': r['closed_deals'],
        'rating': r['rating'],
    } for r in rows[:top_n]]


def refresh_leaderboard(org):
    """Force-rebuild the sorted set. Call after agent stats change."""
    _rebuild_leaderboard(org)
