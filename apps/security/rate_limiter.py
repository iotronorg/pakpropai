import logging

logger = logging.getLogger('security.rate_limiter')

WINDOW_SECONDS    = 60
FAILURE_THRESHOLD = 3
BLOCK_TTL_SECONDS = 300


def _cache():
    from django.core.cache import cache
    return cache


class ConsecutiveAuthFailureLimiter:

    @staticmethod
    def _counter_key(identifier: str) -> str:
        return f'security:authfail:{identifier}'

    @staticmethod
    def _blocked_key(identifier: str) -> str:
        return f'security:blocked:{identifier}'

    @classmethod
    def is_blocked(cls, identifier: str) -> bool:
        try:
            return bool(_cache().get(cls._blocked_key(identifier)))
        except Exception as exc:
            logger.warning('ConsecutiveAuthFailureLimiter.is_blocked error: %s', exc)
            return False

    @classmethod
    def record_auth_failure(cls, identifier: str) -> bool:
        try:
            c = _cache()
            key = cls._counter_key(identifier)
            try:
                count = c.incr(key)
            except ValueError:
                c.set(key, 1, WINDOW_SECONDS)
                count = 1
            if count >= FAILURE_THRESHOLD:
                c.set(cls._blocked_key(identifier), '1', BLOCK_TTL_SECONDS)
                cls._log_security_event(identifier, count)
                logger.warning(
                    'ConsecutiveAuthFailureLimiter: blocked %s for %ds after %d failures',
                    identifier, BLOCK_TTL_SECONDS, FAILURE_THRESHOLD,
                )
                return True
            return False
        except Exception as exc:
            logger.warning('ConsecutiveAuthFailureLimiter.record_auth_failure error: %s', exc)
            return False

    @classmethod
    def record_threat(cls, identifier: str) -> bool:
        return cls.record_auth_failure(identifier)

    @classmethod
    def reset(cls, identifier: str) -> None:
        try:
            c = _cache()
            c.delete(cls._counter_key(identifier))
            c.delete(cls._blocked_key(identifier))
        except Exception as exc:
            logger.warning('ConsecutiveAuthFailureLimiter.reset error: %s', exc)

    @classmethod
    def get_failure_count(cls, identifier: str) -> int:
        try:
            val = _cache().get(cls._counter_key(identifier))
            return int(val) if val is not None else 0
        except Exception:
            return 0

    @classmethod
    def _log_security_event(cls, identifier: str, count: int) -> None:
        try:
            from apps.security.models import ApiSecurityEvent
            ApiSecurityEvent.objects.create(
                event_type=ApiSecurityEvent.EventType.RATE_LIMIT_BLOCKED,
                severity=ApiSecurityEvent.Severity.HIGH,
                ip_address=identifier if ':' not in identifier and '.' in identifier else None,
                threat_detail=f'IP auto-blocked after {count} consecutive auth failures within {WINDOW_SECONDS}s',
            )
        except Exception as exc:
            logger.error('ConsecutiveAuthFailureLimiter: event log failed: %s', exc)
