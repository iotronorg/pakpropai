"""
Custom Prometheus metrics for RealTron AI business events.

Import and call these from the relevant app views/services.
All standard Django metrics (HTTP latency, DB queries, cache hits) are
provided automatically by django_prometheus middleware.
"""
from prometheus_client import Counter, Histogram, Gauge

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

ai_request_duration_seconds = Histogram(
    'realtron_ai_request_duration_seconds',
    'Time to produce an AI reply (seconds)',
    ['route'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
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
    'Inbound webhook events',
    ['gateway', 'result'],  # gateway: stripe|safepay|bsecure|whatsapp; result: ok|invalid_sig|duplicate|error
)
