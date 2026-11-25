"""
Custom Prometheus metrics for RealTron AI business events, and their export (ADR-050).

Import and call these from the relevant app views/services.
All standard Django metrics (HTTP latency, DB queries, cache hits) are
provided automatically by django_prometheus middleware.

Export. A metric lives in the process that observed it. Gunicorn runs several
workers and Celery several pool processes, so with more than one process
``PROMETHEUS_MULTIPROC_DIR`` must name an empty, writable directory, cleared when
the container starts (the entrypoints do it): every process then writes its
samples there and :func:`metrics_registry` adds them up. Celery workers serve
their own registry on ``WORKER_METRICS_PORT`` (``config/celery.py``): AI replies
to WhatsApp, inbound webhook processing and the outbox deliveries all run there,
so a web-only scrape never sees them.
"""
from __future__ import annotations

import os

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, REGISTRY

# ── WhatsApp ──────────────────────────────────────────────────────────────────

whatsapp_messages_total = Counter(
    'realtron_whatsapp_messages_total',
    'WhatsApp messages processed',
    ['message_type', 'direction'],  # message_type: text|image|audio|document; direction: inbound|outbound
)

# ── AI Layer ──────────────────────────────────────────────────────────────────

ai_requests_total = Counter(
    'realtron_ai_requests_total',
    'AI requests handled',
    ['intent', 'route'],  # route: direct|llm|guardrail_blocked
)

# 8.0 is the AI-reply SLO threshold (NFR, full reply p95 <= 8 s).
ai_request_duration_seconds = Histogram(
    'realtron_ai_request_duration_seconds',
    'Time to produce an AI reply (seconds)',
    ['route'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 8.0, 10.0, 15.0, 30.0, 60.0],
)

# ── Deal Locks ────────────────────────────────────────────────────────────────

deal_locks_total = Counter(
    'realtron_deal_locks_total',
    'Deal lock status transitions',
    ['status'],  # initiated|locked|released|cancelled|expired|disputed
)

active_deals_gauge = Gauge(
    'realtron_active_deals',
    'Number of currently active (INITIATED or LOCKED) deal locks',
    multiprocess_mode='max',
)

# ── Leads ─────────────────────────────────────────────────────────────────────

leads_created_total = Counter(
    'realtron_leads_created_total',
    'Leads created (auto from WhatsApp or manual)',
    ['source'],  # whatsapp|manual|web
)

# ── Webhooks ──────────────────────────────────────────────────────────────────

webhook_events_total = Counter(
    'realtron_webhook_events_total',
    'Inbound webhook events at the HTTP edge',
    # gateway: stripe|safepay|bsecure|whatsapp
    # result: ok|invalid_sig|duplicate|rate_limited|bad_payload|enqueue_error
    ['gateway', 'result'],
)

webhook_processed_total = Counter(
    'realtron_webhook_processed_total',
    'Inbound webhook messages after background processing',
    ['gateway', 'result'],  # result: ok|error|retry|dead_letter
)

# Webhook receipt → the worker starts on it. 5 s is the SLO threshold.
webhook_queue_delay_seconds = Histogram(
    'realtron_webhook_queue_delay_seconds',
    'Time from webhook receipt to the start of background processing (seconds)',
    ['gateway'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0],
)

# ── Notification delivery (MVP criterion 4) ───────────────────────────────────

# Notification created (in the business transaction) → delivered to the channel.
# 60 s is the SLO threshold for enquiries.
notification_delivery_seconds = Histogram(
    'realtron_notification_delivery_seconds',
    'Time from a notification being created to its delivery on a channel (seconds)',
    ['event', 'channel'],  # event: enquiry.new|enquiry.message|enquiry.reply|other; channel: in_app|whatsapp|email
    buckets=[0.5, 1.0, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 300.0, 900.0],
)

notification_delivery_failures_total = Counter(
    'realtron_notification_delivery_failures_total',
    'Notifications that could not be delivered on a channel',
    ['event', 'channel', 'reason'],  # reason: permanent|exhausted|preferences
)


outbox_deliveries_total = Counter(
    'realtron_outbox_deliveries_total',
    'Outbox delivery attempts by outcome',
    ['kind', 'result'],  # result: sent|retry|failed|expired
)


def metric_event(event: str) -> str:
    """Bounded label values: only known notification events get their own series."""
    return event if event in KNOWN_EVENTS else 'other'


KNOWN_EVENTS = frozenset({'enquiry.new', 'enquiry.message', 'enquiry.reply'})


def observe_delivery(event: str, channel: str, created_at) -> None:
    """Record one delivery; ``created_at`` is when the notification row was written."""
    from django.utils import timezone
    if created_at is None:
        return
    seconds = max((timezone.now() - created_at).total_seconds(), 0.0)
    notification_delivery_seconds.labels(event=metric_event(event), channel=channel).observe(seconds)


def delivery_failed(event: str, channel: str, reason: str) -> None:
    notification_delivery_failures_total.labels(
        event=metric_event(event), channel=channel, reason=reason).inc()


# ── Export ────────────────────────────────────────────────────────────────────

def multiprocess_dir() -> str:
    return os.environ.get('PROMETHEUS_MULTIPROC_DIR') or os.environ.get('prometheus_multiproc_dir') or ''


def metrics_registry(*extra_collectors) -> CollectorRegistry:
    """
    The registry to export: every process's samples in multiprocess mode, the
    process's own default registry otherwise. ``extra_collectors`` (collectors read
    at scrape time, e.g. :class:`apps.core.slo.OutboxBacklogCollector`) are added
    to a fresh registry so the default one never holds them twice.
    """
    if multiprocess_dir():
        from prometheus_client import multiprocess
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    elif extra_collectors:
        registry = CollectorRegistry()
        registry.register(_DefaultRegistryCollector())
    else:
        return REGISTRY
    for collector in extra_collectors:
        registry.register(collector)
    return registry


class _DefaultRegistryCollector:
    """Re-exposes the default registry inside another registry."""

    def collect(self):
        return REGISTRY.collect()
