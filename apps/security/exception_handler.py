import ipaddress
import logging

from rest_framework.views import exception_handler as drf_default_handler

logger = logging.getLogger('security.exception_handler')


def _is_internal_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        return False


def security_exception_handler(exc, context):
    response = drf_default_handler(exc, context)
    if response is None:
        return response

    if response.status_code not in (401, 403):
        return response

    request = context.get('request')
    if not request:
        return response

    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    ip = xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '')

    if not ip or _is_internal_ip(ip):
        return response

    try:
        from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
        blocked = ConsecutiveAuthFailureLimiter.record_auth_failure(ip)
        if blocked:
            logger.warning('security_exception_handler: %s auto-blocked after auth failures', ip)
    except Exception as exc_inner:
        logger.warning('security_exception_handler: rate limiter call failed: %s', exc_inner)

    return response
