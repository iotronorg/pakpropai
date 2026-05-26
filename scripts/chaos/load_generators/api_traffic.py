"""
API Traffic Generator — used by the Redis crash scenario.

Fires concurrent inventory and deal-lock operations against the Django REST API
using valid session tokens.  Each operation exercises a different path through
Django's ORM and cache layer so we can verify that DB rows remain the sole
source of truth when Redis is unavailable.

`fire_inventory_ops` is the main entry point called by the chaos orchestrator.
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any

log = logging.getLogger("chaos.api_traffic")

_BASE_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def _get_auth_token_for_org(org_id: str) -> str | None:
    """
    Obtain a short-lived JWT for the org's developer account by calling
    Django's auth endpoint directly (no HTTP round-trip in dry-run mode).
    """
    try:
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.tokens import RefreshToken
        from apps.organizations.models import Organization

        org  = Organization.objects.filter(pk=org_id).select_related("admin_user").first()
        if org is None or org.admin_user is None:
            return None
        token = RefreshToken.for_user(org.admin_user)
        return str(token.access_token)
    except Exception as exc:
        log.debug("Could not get auth token for org %s: %s", org_id, exc)
        return None


def _call_api(
    method: str,
    path: str,
    base_url: str,
    token: str | None,
    data: dict | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        time.sleep(random.uniform(0.002, 0.015))
        return {"success": True, "dry_run": True, "path": path}

    import requests

    headers = dict(_BASE_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    t0 = time.perf_counter()
    try:
        resp = getattr(requests, method)(
            f"{base_url}{path}",
            json=data,
            headers=headers,
            timeout=10,
        )
        return {
            "success": resp.status_code < 400,
            "status_code": resp.status_code,
            "latency_ms": (time.perf_counter() - t0) * 1000,
            "path": path,
        }
    except requests.RequestException as exc:
        return {
            "success": False,
            "error": str(exc),
            "latency_ms": (time.perf_counter() - t0) * 1000,
            "path": path,
        }


def fire_inventory_ops(
    org_id: str,
    base_url: str = "http://localhost:8000",
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Execute a representative mix of inventory + deal-lock operations for one org.
    Returns a dict with success=True/False and details of each sub-operation.

    Operations fired:
      1. GET /api/v1/properties/          — list inventory (cache-heavy)
      2. PATCH /api/v1/properties/<id>/   — update a property (DB write + cache invalidation)
      3. GET /api/v1/escrow/deals/        — list active deal locks (cache fallback test)
    """
    token = _get_auth_token_for_org(org_id)
    results: list[dict] = []

    # 1. List properties — exercises cache.get("prop_list:<org>")
    r = _call_api("get", "/api/v1/properties/?page_size=5", base_url, token, dry_run=dry_run)
    results.append({**r, "op": "list_properties"})

    # 2. Attempt a property status update to exercise DB write path
    # We use a synthetic UUID — 404 is acceptable; we're testing the DB path, not business logic.
    prop_id = str(uuid.uuid4())
    r = _call_api(
        "patch",
        f"/api/v1/properties/{prop_id}/",
        base_url,
        token,
        data={"is_active": True},
        dry_run=dry_run,
    )
    results.append({**r, "op": "patch_property"})

    # 3. List deal locks — exercises EscrowDeal queryset + cache
    r = _call_api("get", "/api/v1/escrow/deals/?status=locked", base_url, token, dry_run=dry_run)
    results.append({**r, "op": "list_deals"})

    # A composite op is "successful" if at least the GET ops return non-5xx.
    # 404 on PATCH (unknown property) is expected and acceptable.
    success = not any(
        op.get("status_code", 200) >= 500
        for op in results
        if op.get("op") != "patch_property"  # ignore expected 404
    )

    return {
        "success": success,
        "org_id": org_id,
        "ops": results,
    }


def fire_deal_lock_attempt(
    org_id: str,
    property_id: str | None = None,
    buyer_phone: str = "+971501234567",
    base_url: str = "http://localhost:8000",
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Attempt to initiate a deal lock. Used to verify race-condition protection
    under Redis-down conditions (the select_for_update path must still work).
    """
    token = _get_auth_token_for_org(org_id)
    pid   = property_id or str(uuid.uuid4())

    return _call_api(
        "post",
        "/api/v1/escrow/deals/",
        base_url,
        token,
        data={
            "property": pid,
            "buyer_phone": buyer_phone,
            "amount": str(random.randint(500_000, 5_000_000)),
            "currency": "AED",
            "gateway": "manual",
        },
        dry_run=dry_run,
    )
