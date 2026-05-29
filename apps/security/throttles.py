from rest_framework.throttling import BaseThrottle
from rest_framework.exceptions import Throttled


class BlockedIPThrottle(BaseThrottle):

    def get_client_ip(self, request) -> str:
        xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

    def allow_request(self, request, view) -> bool:
        ip = self.get_client_ip(request)
        if not ip:
            return True
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        if ConsecutiveAuthFailureLimiter.is_blocked(ip):
            self.ip = ip
            return False
        return True

    def wait(self):
        from apps.security.rate_limiter import BLOCK_TTL_SECONDS
        return float(BLOCK_TTL_SECONDS)

    def throttle_failure(self):
        raise Throttled(detail='IP temporarily blocked due to repeated authentication failures.')
