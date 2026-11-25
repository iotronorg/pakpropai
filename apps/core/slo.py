"""
Service-level objectives: what is measured, against which target (ADR-050).

The Prometheus rules in ``ops/prometheus-alerts.yaml`` and the dashboard in
``ops/grafana/dashboards/realtron-slo.json`` implement these objectives;
``tests/test_slo.py`` checks that they agree with this table.

Two readings come from the database rather than from process counters, because
they must survive restarts and cover every worker:

* :class:`OutboxBacklogCollector`: undelivered outbox messages per kind and the
  age of the oldest, read when ``/metrics/`` is scraped (cached briefly);
* :func:`delivery_report`: enquiry notification delivery per channel against
  the one-minute target (MVP criterion 4), for ``manage.py notification_delivery_report``.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, Min
from django.utils import timezone

# name → (objective, threshold seconds or None, description). Ratios over 30 days.
OBJECTIVES: dict[str, tuple[float, float | None, str]] = {
    'api_availability':      (0.995, None, 'REST requests answered without a 5xx'),
    'api_latency':           (0.95, 0.3, 'REST requests (non-AI, non-search) answered within 300 ms'),
    'search_latency':        (0.95, 0.4, 'Search requests answered within 400 ms'),
    'ai_reply_latency':      (0.95, 8.0, 'Model-written assistant replies produced within 8 s'),
    'webhook_processing':    (0.995, None, 'Inbound WhatsApp messages processed without error'),
    'webhook_queue_delay':   (0.95, 5.0, 'Inbound WhatsApp messages picked up within 5 s of receipt'),
    'enquiry_notification':  (0.99, 60.0, 'Enquiry notifications delivered within 60 s, per channel'),
}

# Views that count as search (django_prometheus ``view`` label = URL name).
SEARCH_VIEWS = ('listing-search', 'public-listing-search', 'property-list')
# Views excluded from the REST latency objective: AI turns, streaming, the scrape itself.
NON_REST_VIEWS = ('conversation-messages', 'prometheus-metrics')

ENQUIRY_TARGET_SECONDS = OBJECTIVES['enquiry_notification'][1]


# ── Outbox backlog, read at scrape time ──────────────────────────────────────

class OutboxBacklogCollector:
    """
    ``realtron_outbox_undelivered{kind}`` and ``realtron_outbox_oldest_undelivered_seconds{kind}``.

    One grouped query on the ``(status, available_at)`` index, cached for
    ``ttl`` seconds per process so a burst of scrapes costs one query. A DB error
    yields no samples (the scrape still succeeds and ``absent()`` alerts can fire).
    """

    def __init__(self, ttl: float = 15.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._at = 0.0
        self._rows: list[tuple[str, int, float]] = []

    def describe(self):
        return []

    def _read(self) -> list[tuple[str, int, float]]:
        from apps.core.models import OutboxMessage
        now = timezone.now()
        rows = (OutboxMessage.objects
                .filter(status__in=[OutboxMessage.Status.PENDING, OutboxMessage.Status.SENDING])
                .values('kind').annotate(n=Count('id'), oldest=Min('created_at')).order_by())
        return [(r['kind'], r['n'], max((now - r['oldest']).total_seconds(), 0.0)) for r in rows]

    def collect(self):
        from prometheus_client.core import GaugeMetricFamily
        import logging
        with self._lock:
            if time.monotonic() - self._at > self.ttl:
                try:
                    self._rows = self._read()
                    self._at = time.monotonic()
                except Exception:
                    logging.getLogger(__name__).warning('slo.outbox_backlog_failed', exc_info=True)
                    self._rows, self._at = [], 0.0
            rows = list(self._rows)
        count = GaugeMetricFamily('realtron_outbox_undelivered',
                                  'Outbox messages not yet delivered (pending or sending)', labels=['kind'])
        age = GaugeMetricFamily('realtron_outbox_oldest_undelivered_seconds',
                                'Age of the oldest undelivered outbox message (seconds)', labels=['kind'])
        from apps.core.outbox import HANDLERS
        seen = {kind for kind, _, _ in rows}
        for kind, n, oldest in rows:
            count.add_metric([kind], n)
            age.add_metric([kind], oldest)
        for kind in HANDLERS:              # explicit zeros: "no backlog" is a reading, not a gap
            if kind not in seen:
                count.add_metric([kind], 0)
                age.add_metric([kind], 0)
        yield count
        yield age


outbox_backlog = OutboxBacklogCollector()


# ── Enquiry notification delivery report (MVP criterion 4) ───────────────────

@dataclass
class ChannelReport:
    channel: str
    total: int = 0            # notifications that should reach this channel
    delivered: int = 0
    within_target: int = 0
    late_pending: int = 0     # not delivered and already older than the target
    pending: int = 0          # not delivered, still inside the target
    failed: int = 0           # failed or expired
    p50: float | None = None
    p95: float | None = None
    max: float | None = None

    @property
    def ratio(self) -> float | None:
        """Share delivered within target, of those whose outcome is known."""
        decided = self.total - self.pending
        return None if decided <= 0 else self.within_target / decided

    def as_dict(self, target: float) -> dict:
        r = self.ratio
        return {'channel': self.channel, 'total': self.total, 'delivered': self.delivered,
                'within_target': self.within_target, 'late_pending': self.late_pending,
                'pending': self.pending, 'failed': self.failed,
                'p50_s': self.p50, 'p95_s': self.p95, 'max_s': self.max,
                'ratio_within_target': None if r is None else round(r, 4),
                'meets_objective': None if r is None else r >= OBJECTIVES['enquiry_notification'][0],
                'target_s': target}


def _percentile(sorted_values: list[float], q: float) -> float | None:
    """Nearest-rank percentile: the smallest value with at least ``q`` of the values at or below it."""
    import math
    if not sorted_values:
        return None
    k = max(0, min(len(sorted_values) - 1, math.ceil(q * len(sorted_values)) - 1))
    return round(sorted_values[k], 3)


def delivery_report(since: datetime, until: datetime | None = None, *, event_prefix: str = 'enquiry.',
                    target: float = ENQUIRY_TARGET_SECONDS) -> dict:
    """
    Delivery of notifications whose ``event`` starts with ``event_prefix``,
    created in ``[since, until)``, per channel.

    * ``in_app``: the inbox row commits with the enquiry message, so it is
      delivered at creation (latency 0) — counted so the totals line up.
    * ``whatsapp`` / ``email``: from the outbox rows queued with the notification
      (keys ``notification:<id>`` and ``email:<id>``), latency ``sent_at − created_at``
      of the notification. Sent outbox rows are purged after 7 days, so a window
      that starts earlier under-counts; the report says so.
    """
    from apps.core.models import OutboxMessage
    from apps.notifications.models import Notification

    now = timezone.now()
    until = until or now
    notes = []
    if since < now - timedelta(days=7):
        notes.append('Window starts more than 7 days ago: sent outbox rows are purged after 7 days, '
                     'so WhatsApp and email counts are incomplete.')
    created = dict(Notification.objects.filter(
        event__startswith=event_prefix, created_at__gte=since, created_at__lt=until,
    ).values_list('pk', 'created_at'))

    channels = {'in_app': ChannelReport('in_app', total=len(created), delivered=len(created),
                                        within_target=len(created))}
    if created:
        channels['in_app'].p50 = channels['in_app'].p95 = channels['in_app'].max = 0.0
    latencies: dict[str, list[float]] = {'whatsapp': [], 'email': []}
    for ch in latencies:
        channels[ch] = ChannelReport(ch)

    ids = [str(pk) for pk in created]
    for start in range(0, len(ids), 1000):
        chunk = ids[start:start + 1000]
        keys = [f'notification:{i}' for i in chunk] + [f'email:{i}' for i in chunk]
        rows = OutboxMessage.objects.filter(idempotency_key__in=keys).values_list(
            'idempotency_key', 'status', 'sent_at')
        for key, status, sent_at in rows:
            prefix, _, nid = key.partition(':')
            rep = channels['whatsapp' if prefix == 'notification' else 'email']
            origin = created.get(_uuid(nid))
            if origin is None:
                continue
            rep.total += 1
            if status == OutboxMessage.Status.SENT and sent_at:
                seconds = max((sent_at - origin).total_seconds(), 0.0)
                rep.delivered += 1
                latencies[rep.channel].append(seconds)
                if seconds <= target:
                    rep.within_target += 1
            elif status in (OutboxMessage.Status.FAILED, OutboxMessage.Status.EXPIRED):
                rep.failed += 1
            elif (now - origin).total_seconds() > target:
                rep.late_pending += 1
            else:
                rep.pending += 1
    for ch, values in latencies.items():
        values.sort()
        rep = channels[ch]
        rep.p50, rep.p95 = _percentile(values, 0.50), _percentile(values, 0.95)
        rep.max = round(values[-1], 3) if values else None

    return {'event_prefix': event_prefix, 'since': since.isoformat(), 'until': until.isoformat(),
            'target_s': target, 'objective': OBJECTIVES['enquiry_notification'][0],
            'channels': [channels[c].as_dict(target) for c in ('in_app', 'whatsapp', 'email')],
            'notes': notes}


def _uuid(value):
    import uuid
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None
