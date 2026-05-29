"""
TenantQueueMonitor — detects queue backlogs per org and applies aux-queue isolation.
Beat schedule: every 30 seconds.
"""
from __future__ import annotations

import logging
from celery import shared_task

logger = logging.getLogger(__name__)

_ISOLATION_TTL = 300  # 5 min
_ISOLATED_SET_KEY = 'queue_isolated_orgs'
_ISOLATED_SET_TTL = 600


def _cache():
    from django.core.cache import cache
    return cache


def _get_isolation_threshold() -> int:
    try:
        from apps.config.services import SystemConfigService
        val = SystemConfigService.get('queue_isolation_threshold')
        return int(val) if val else 1000
    except Exception:
        return 1000


def _isolate_tenant(org_id: str) -> None:
    """Mark an org's tasks for auxiliary queue routing."""
    _cache().set(f'aux_route:{org_id}', '1', _ISOLATION_TTL)
    try:
        import json
        isolated = _get_isolated_orgs()
        if org_id not in isolated:
            isolated.append(org_id)
        _cache().set(_ISOLATED_SET_KEY, json.dumps(isolated), _ISOLATED_SET_TTL)
    except Exception:
        pass
    logger.warning('TenantQueueMonitor: isolated org=%s', org_id)


def _clear_isolation(org_id: str) -> None:
    _cache().delete(f'aux_route:{org_id}')
    try:
        import json
        isolated = _get_isolated_orgs()
        if org_id in isolated:
            isolated.remove(org_id)
        _cache().set(_ISOLATED_SET_KEY, json.dumps(isolated), _ISOLATED_SET_TTL)
    except Exception:
        pass


def _get_isolated_orgs() -> list[str]:
    try:
        import json
        raw = _cache().get(_ISOLATED_SET_KEY)
        return json.loads(raw) if raw else []
    except Exception:
        return []


@shared_task(bind=True, name='resilience.monitor_tenant_queues', queue='default')
def monitor_tenant_queues(self):
    """
    Inspect pending Celery task queue depth per org_id.
    Isolates orgs over threshold to the auxiliary queue.
    Fail-open: any inspect/Redis error logs WARNING and returns.
    """
    try:
        from celery import current_app
        inspect = current_app.control.inspect()
        reserved = inspect.reserved() or {}
    except Exception:
        logger.warning('TenantQueueMonitor: celery inspect failed', exc_info=True)
        return

    threshold = _get_isolation_threshold()
    org_counts: dict[str, int] = {}

    for worker_tasks in reserved.values():
        for task in (worker_tasks or []):
            kwargs = task.get('kwargs', {}) or {}
            org_id = kwargs.get('org_id')
            if org_id:
                org_counts[org_id] = org_counts.get(org_id, 0) + 1

    for org_id, count in org_counts.items():
        if count > threshold:
            _isolate_tenant(org_id)
        else:
            if _cache().get(f'aux_route:{org_id}'):
                _clear_isolation(org_id)
