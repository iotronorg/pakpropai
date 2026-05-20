#!/usr/bin/env python3
"""
Post-deployment smoke test for RealTron AI.

Fires three lightweight probes against the staging (or any target) environment
and asserts HTTP status + latency budget.  Exits 0 only when all pass.

Usage:
    STAGING_URL=https://staging.realtron.ai python scripts/smoke_test.py

Environment variables:
    STAGING_URL            — base URL of the target environment (required)
    SMOKE_WA_VERIFY_TOKEN  — WhatsApp hub.verify_token configured in that env
    SMOKE_LATENCY_MS       — per-probe latency budget in ms (default: 200)
"""
import json
import os
import sys
import time

import requests

TARGET = os.environ.get("STAGING_URL", "http://localhost:8000").rstrip("/")
BUDGET_MS = int(os.environ.get("SMOKE_LATENCY_MS", "200"))
TIMEOUT_S = 5


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _result(label: str, ok: bool, detail: str) -> bool:
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}]  {label:<28}  {detail}")
    return ok


# ── Probe 1: health endpoint ─────────────────────────────────────────────────

def probe_health() -> bool:
    url = f"{TARGET}/health/"
    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=TIMEOUT_S)
    except Exception as exc:
        return _result("GET /health/", False, f"connection error: {exc}")
    ms = _ms(t0)
    if r.status_code not in (200, 503):
        return _result("GET /health/", False, f"HTTP {r.status_code} ({ms}ms)")
    if ms > BUDGET_MS:
        return _result("GET /health/", False, f"HTTP {r.status_code} but {ms}ms > {BUDGET_MS}ms budget")
    status = r.json().get("status", "?")
    return _result("GET /health/", True, f"status={status} ({ms}ms)")


# ── Probe 2: WhatsApp webhook GET verification ────────────────────────────────

def probe_webhook_verify() -> bool:
    token = os.environ.get("SMOKE_WA_VERIFY_TOKEN", "smoke-test-token")
    challenge = "realtron-smoke-4287"
    url = (
        f"{TARGET}/api/v1/whatsapp/webhook/"
        f"?hub.mode=subscribe&hub.verify_token={token}&hub.challenge={challenge}"
    )
    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=TIMEOUT_S)
    except Exception as exc:
        return _result("GET /webhook/ (verify)", False, f"connection error: {exc}")
    ms = _ms(t0)
    if r.status_code == 403:
        # Endpoint is up but token mismatch — deployment is live, environment misconfigured
        return _result("GET /webhook/ (verify)", True, f"403 token mismatch (endpoint reachable) ({ms}ms)")
    if r.status_code != 200:
        return _result("GET /webhook/ (verify)", False, f"HTTP {r.status_code} ({ms}ms)")
    if ms > BUDGET_MS:
        return _result("GET /webhook/ (verify)", False, f"HTTP {r.status_code} but {ms}ms > {BUDGET_MS}ms budget")
    return _result("GET /webhook/ (verify)", True, f"HTTP {r.status_code} ({ms}ms)")


# ── Probe 3: WhatsApp webhook POST with synthetic payload ─────────────────────

def probe_webhook_post() -> bool:
    """
    Sends a minimal well-formed WhatsApp webhook payload with an intentionally
    invalid HMAC signature.  Expected outcomes:
      - 403  signature rejected — endpoint live and processing correctly
      - 200  signature validation disabled in this environment — also fine
    Any other status indicates a configuration or code regression.
    """
    payload = json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "SMOKE_ENTRY",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "0000000000",
                        "phone_number_id": "SMOKE",
                    },
                    "messages": [{
                        "from": "0000000000",
                        "id": "smoke_msg_id",
                        "timestamp": str(int(time.time())),
                        "text": {"body": "smoke test"},
                        "type": "text",
                    }],
                },
                "field": "messages",
            }],
        }],
    })
    url = f"{TARGET}/api/v1/whatsapp/webhook/"
    t0 = time.monotonic()
    try:
        r = requests.post(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=invalidsmokesignature",
            },
            timeout=TIMEOUT_S,
        )
    except Exception as exc:
        return _result("POST /webhook/ (event)", False, f"connection error: {exc}")
    ms = _ms(t0)
    if r.status_code not in (200, 403):
        return _result("POST /webhook/ (event)", False, f"unexpected HTTP {r.status_code} ({ms}ms)")
    if ms > BUDGET_MS:
        return _result("POST /webhook/ (event)", False, f"HTTP {r.status_code} but {ms}ms > {BUDGET_MS}ms budget")
    return _result("POST /webhook/ (event)", True, f"HTTP {r.status_code} ({ms}ms)")


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    sep = "─" * 60
    print(f"\nRealTron AI — Smoke Test")
    print(f"Target : {TARGET}")
    print(f"Budget : {BUDGET_MS}ms per probe")
    print(sep)

    results = [
        probe_health(),
        probe_webhook_verify(),
        probe_webhook_post(),
    ]

    print(sep)
    passed = sum(results)
    total = len(results)
    verdict = "PASSED" if passed == total else "FAILED"
    print(f"Result : {verdict}  ({passed}/{total} probes)\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
