"""
WhatsApp Inbound Traffic Generator.

Generates realistic, HMAC-SHA256-signed Meta Cloud API webhook payloads and
delivers them concurrently to the RealTron webhook endpoint.  Each payload
represents a complete WhatsApp message event as Meta would deliver it.

Supported message types: text, audio (stub), image (stub).

The generator is intentionally stateless — each call to `fire_conversation_burst`
spawns a fresh thread pool and returns a `TrafficResult` with per-message outcomes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import string
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Literal

import requests

log = logging.getLogger("chaos.wa_traffic")

MessageType = Literal["text", "audio", "image"]

# Realistic conversation starters that will exercise the IntentClassifier
_TEXT_CORPUS = [
    "I'm looking for a 3 bedroom apartment in Dubai Marina under 2M AED",
    "Can you show me properties near downtown with sea view?",
    "What are the latest listings in Business Bay?",
    "I want to lock a deal on property ID 4521",
    "Check loan eligibility for 1.5 million AED",
    "I need to verify my documents for property purchase",
    "Show me 2BHK flats in JLT area",
    "What is the current market price per sqft in Palm Jumeirah?",
    "I want to schedule a viewing for a villa in Arabian Ranches",
    "Can I talk to an agent about my inquiry?",
    "مجھے 3 بیڈروم اپارٹمنٹ چاہیے",
    "أريد شقة في دبي مارينا",
    "I'd like to see properties with ROI above 7%",
    "What documents do I need to buy property as a foreigner?",
    "Show me off-plan projects in Dubai Hills Estate",
]

_PHONE_PREFIXES = ["+971", "+44", "+1", "+92"]


@dataclass
class MessageOutcome:
    msg_id: str
    phone: str
    msg_type: MessageType
    status_code: int
    latency_ms: float
    error: str = ""

    @property
    def accepted(self) -> bool:
        return self.status_code == 200


@dataclass
class TrafficResult:
    dispatched: int = 0
    accepted: int = 0
    rejected: int = 0
    errored: int = 0
    avg_latency_ms: float = 0.0
    outcomes: list[MessageOutcome] = field(default_factory=list)

    def finalize(self) -> None:
        self.accepted  = sum(1 for o in self.outcomes if o.accepted)
        self.rejected  = sum(1 for o in self.outcomes if not o.accepted and not o.error)
        self.errored   = sum(1 for o in self.outcomes if o.error)
        latencies      = [o.latency_ms for o in self.outcomes if o.latency_ms > 0]
        self.avg_latency_ms = sum(latencies) / len(latencies) if latencies else 0.0


class WhatsAppTrafficGenerator:
    def __init__(
        self,
        concurrency: int = 20,
        dry_run: bool = False,
        base_url: str = "http://localhost:8000",
        app_secret: str | None = None,
        phone_number_id: str = "1234567890",
    ):
        self.concurrency      = concurrency
        self.dry_run          = dry_run
        self.base_url         = base_url.rstrip("/")
        self.phone_number_id  = phone_number_id
        self._app_secret      = app_secret or self._read_app_secret()
        self._session         = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def _read_app_secret(self) -> str:
        try:
            from django.conf import settings
            return getattr(settings, "WA_APP_SECRET", "chaos-test-secret") or "chaos-test-secret"
        except Exception:
            return "chaos-test-secret"

    # ── Payload factories ────────────────────────────────────────────────────

    def _random_phone(self) -> str:
        prefix = random.choice(_PHONE_PREFIXES)
        suffix = "".join(random.choices(string.digits, k=9))
        return f"{prefix}{suffix}"

    def _random_msg_id(self) -> str:
        return "wamid." + uuid.uuid4().hex[:28]

    def _build_text_payload(
        self, phone: str, msg_id: str, body: str
    ) -> dict:
        ts = str(int(time.time()))
        return {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "CHAOS_TEST_ENTRY",
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550000001",
                            "phone_number_id": self.phone_number_id,
                        },
                        "contacts": [{
                            "profile": {"name": f"Chaos Tester {phone[-4:]}"},
                            "wa_id": phone.lstrip("+"),
                        }],
                        "messages": [{
                            "from": phone.lstrip("+"),
                            "id": msg_id,
                            "timestamp": ts,
                            "text": {"body": body},
                            "type": "text",
                        }],
                    },
                    "field": "messages",
                }],
            }],
        }

    def _build_audio_payload(self, phone: str, msg_id: str) -> dict:
        ts = str(int(time.time()))
        return {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "CHAOS_TEST_ENTRY",
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550000001",
                            "phone_number_id": self.phone_number_id,
                        },
                        "contacts": [{"profile": {"name": f"Voice {phone[-4:]}"}, "wa_id": phone.lstrip("+")}],
                        "messages": [{
                            "from": phone.lstrip("+"),
                            "id": msg_id,
                            "timestamp": ts,
                            "type": "audio",
                            "audio": {
                                "id": "CHAOS_MEDIA_" + uuid.uuid4().hex[:12],
                                "mime_type": "audio/ogg; codecs=opus",
                            },
                        }],
                    },
                    "field": "messages",
                }],
            }],
        }

    def _sign_payload(self, body_bytes: bytes) -> str:
        sig = hmac.new(
            self._app_secret.encode(),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={sig}"

    # ── Single message dispatch ──────────────────────────────────────────────

    def _dispatch_one(
        self,
        idx: int,
        msg_type: MessageType = "text",
    ) -> MessageOutcome:
        phone  = self._random_phone()
        msg_id = self._random_msg_id()

        if msg_type == "audio":
            payload = self._build_audio_payload(phone, msg_id)
        else:
            body    = random.choice(_TEXT_CORPUS)
            payload = self._build_text_payload(phone, msg_id, body)

        body_bytes = json.dumps(payload, separators=(",", ":")).encode()
        sig        = self._sign_payload(body_bytes)

        if self.dry_run:
            log.debug("[DRY RUN] Would dispatch msg_id=%s type=%s", msg_id, msg_type)
            time.sleep(random.uniform(0.005, 0.02))  # simulate network
            return MessageOutcome(
                msg_id=msg_id, phone=phone, msg_type=msg_type,
                status_code=200, latency_ms=random.uniform(10, 80),
            )

        t0 = time.perf_counter()
        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/webhook/whatsapp/",
                data=body_bytes,
                headers={
                    "Content-Type":     "application/json",
                    "X-Hub-Signature-256": sig,
                },
                timeout=15,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            return MessageOutcome(
                msg_id=msg_id,
                phone=phone,
                msg_type=msg_type,
                status_code=resp.status_code,
                latency_ms=latency_ms,
            )
        except requests.RequestException as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            return MessageOutcome(
                msg_id=msg_id,
                phone=phone,
                msg_type=msg_type,
                status_code=0,
                latency_ms=latency_ms,
                error=str(exc),
            )

    # ── Public API ───────────────────────────────────────────────────────────

    def fire_conversation_burst(
        self,
        count: int,
        msg_type: MessageType = "text",
        timeout_s: float = 60,
    ) -> TrafficResult:
        """
        Fire `count` concurrent WhatsApp messages and return aggregated outcomes.
        Uses a thread pool capped at `self.concurrency` workers.
        """
        result = TrafficResult(dispatched=count)
        workers = min(self.concurrency, count)
        log.info("Firing %d %s messages via %d threads…", count, msg_type, workers)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chaos-wa") as pool:
            futures = {
                pool.submit(self._dispatch_one, i, msg_type): i
                for i in range(count)
            }
            for future in as_completed(futures, timeout=timeout_s):
                try:
                    outcome = future.result()
                    result.outcomes.append(outcome)
                    log.debug(
                        "msg_id=%s status=%d latency=%.1fms",
                        outcome.msg_id, outcome.status_code, outcome.latency_ms,
                    )
                except Exception as exc:
                    log.warning("Future raised: %s", exc)

        result.finalize()
        log.info(
            "Burst complete — dispatched=%d accepted=%d rejected=%d errored=%d avg=%.1fms",
            result.dispatched, result.accepted, result.rejected,
            result.errored, result.avg_latency_ms,
        )
        return result

    def fire_mixed_burst(self, count: int, audio_pct: float = 0.2) -> TrafficResult:
        """Fire a mixed workload: `audio_pct` fraction are audio messages."""
        result = TrafficResult(dispatched=count)
        workers = min(self.concurrency, count)
        log.info(
            "Firing mixed burst: %d messages (%.0f%% audio) via %d threads",
            count, audio_pct * 100, workers,
        )

        def _typed(idx: int) -> MessageOutcome:
            mt: MessageType = "audio" if random.random() < audio_pct else "text"
            return self._dispatch_one(idx, mt)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chaos-wa-mix") as pool:
            futures = [pool.submit(_typed, i) for i in range(count)]
            for future in as_completed(futures, timeout=120):
                try:
                    result.outcomes.append(future.result())
                except Exception:
                    pass

        result.finalize()
        return result
