import logging
import math
import time
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_TRACKED_ROUTES = [
    "whatsapp.webhook",
    "celery.process_whatsapp",
    "celery.sync_platform",
    "postgres.transaction_lock",
    "meta.outbound_payload",
]
_LATENCY_TTL = 86400


def _redis():
    from django.core.cache import caches
    try:
        return caches["default"].client.get_client()
    except Exception:
        import redis as _redis_lib
        from django.conf import settings
        url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
        return _redis_lib.from_url(url)


class MetricsCollector:

    @staticmethod
    def get_error_distribution(org_id: Optional[str] = None, hours: int = 24) -> dict:
        try:
            r = _redis()
            today = date.today().isoformat()
            raw = r.hgetall(f"otel:errors:{today}")
            return {k.decode(): int(v) for k, v in raw.items()}
        except Exception:
            logger.warning("MetricsCollector.get_error_distribution failed", exc_info=True)
            return {}

    @staticmethod
    def get_dlq_depth() -> int:
        try:
            from celery import current_app
            inspector = current_app.control.inspect(timeout=1.0)
            reserved = inspector.reserved() or {}
            total = sum(len(v) for v in reserved.values())
            return total
        except Exception:
            logger.warning("MetricsCollector.get_dlq_depth failed", exc_info=True)
            return 0

    @staticmethod
    def get_p99_latencies(hours: int = 24) -> dict:
        results = {}
        try:
            r = _redis()
            for route in _TRACKED_ROUTES:
                key = f"otel:latency:{route}"
                try:
                    card = r.zcard(key)
                    if card == 0:
                        results[route] = 0.0
                        continue
                    idx = max(0, math.ceil(0.99 * card) - 1)
                    entries = r.zrange(key, idx, idx, withscores=True)
                    if entries:
                        # score = unix_ts + duration_ms; extract duration
                        score = entries[0][1]
                        ts_floor = int(score) - (int(score) % 100000)
                        duration = score - ts_floor
                        results[route] = round(duration, 2)
                    else:
                        results[route] = 0.0
                except Exception:
                    results[route] = 0.0
        except Exception:
            logger.warning("MetricsCollector.get_p99_latencies failed", exc_info=True)
        return results

    @staticmethod
    def get_active_connections() -> dict:
        result = {"websocket": 0, "celery_workers": 0, "redis": 0, "postgres": 0}
        try:
            r = _redis()
            result["redis"] = len(r.client_list())
        except Exception:
            pass
        try:
            from celery import current_app
            inspector = current_app.control.inspect(timeout=1.0)
            active = inspector.active() or {}
            result["celery_workers"] = sum(len(v) for v in active.values())
        except Exception:
            pass
        try:
            from django.db import connection
            with connection.cursor() as c:
                c.execute("SELECT count(*) FROM pg_stat_activity")
                result["postgres"] = c.fetchone()[0]
        except Exception:
            pass
        return result

    @staticmethod
    def record_latency(route: str, duration_ms: float) -> None:
        try:
            r = _redis()
            key = f"otel:latency:{route}"
            # Score = unix_ts * 100000 + duration_ms to allow extraction
            score = int(time.time()) * 100000 + duration_ms
            r.zadd(key, {f"{time.time()}:{duration_ms}": score})
            r.expire(key, _LATENCY_TTL)
        except Exception:
            logger.warning("MetricsCollector.record_latency failed", exc_info=True)

    @staticmethod
    def record_error(status_code: int, error_type: str) -> None:
        try:
            r = _redis()
            today = date.today().isoformat()
            key = f"otel:errors:{today}"
            field = f"{error_type}:{status_code}"
            r.hincrby(key, field, 1)
            r.expire(key, _LATENCY_TTL)
        except Exception:
            logger.warning("MetricsCollector.record_error failed", exc_info=True)

    @staticmethod
    def get_percentiles(route: str, hours: int = 24) -> dict:
        """Return P50/P95/P99 for a single route."""
        try:
            r = _redis()
            key = f"otel:latency:{route}"
            card = r.zcard(key)
            if card == 0:
                return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "sample_count": 0}

            def _get_at(pct):
                idx = max(0, math.ceil(pct * card) - 1)
                entries = r.zrange(key, idx, idx, withscores=True)
                if entries:
                    score = entries[0][1]
                    ts_floor = int(score) - (int(score) % 100000)
                    return round(score - ts_floor, 2)
                return 0.0

            return {
                "p50_ms": _get_at(0.50),
                "p95_ms": _get_at(0.95),
                "p99_ms": _get_at(0.99),
                "sample_count": card,
            }
        except Exception:
            logger.warning("MetricsCollector.get_percentiles failed", exc_info=True)
            return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "sample_count": 0}
