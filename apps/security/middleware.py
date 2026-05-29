import ipaddress
import logging

from django.http import JsonResponse

from apps.security.threat_detection import ThreatDetectionEngine


def _is_internal_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        return False

logger = logging.getLogger('security.middleware')

_EXEMPT_PREFIXES = (
    '/api/v1/whatsapp/webhook',
    '/admin/jsi18n/',
    '/static/',
    '/media/',
)

_API_PREFIX = '/api/'


def _get_client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _extract_body_data(request) -> dict:
    content_type = request.content_type or ''
    if 'json' not in content_type:
        return {}
    try:
        import json
        return json.loads(request.body[:65536]) or {}
    except Exception:
        return {}


def _get_auth_context(request) -> tuple:
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return '', ''
    user_id = str(getattr(user, 'id', '') or '')
    org = getattr(user, 'owned_organization', None)
    org_id = str(org.id) if org else ''
    if not org_id:
        membership = getattr(user, 'org_memberships', None)
        if membership is not None:
            try:
                m = membership.filter(is_active=True).select_related('organization').first()
                if m:
                    org_id = str(m.organization_id)
            except Exception:
                pass
    return user_id, org_id


class ApiSecurityMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        path = request.path_info
        if not path.startswith(_API_PREFIX):
            return None
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return None

        # Pre-auth block check (external IPs only — never block internal/loopback)
        ip = _get_client_ip(request)
        if not _is_internal_ip(ip):
            try:
                from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
                from django.http import JsonResponse as _JR
                if ConsecutiveAuthFailureLimiter.is_blocked(ip):
                    return _JR({'detail': 'Too many requests — IP temporarily blocked.'}, status=429)
            except Exception as exc:
                logger.warning('ApiSecurityMiddleware: block check error: %s', exc)

        user_id, org_id = _get_auth_context(request)

        threat = ThreatDetectionEngine.scan(
            query_params=dict(request.GET),
            body_data=_extract_body_data(request),
            request_meta=request.META,
            auth_header=request.META.get('HTTP_AUTHORIZATION', ''),
            authenticated_user_id=user_id,
            authenticated_org_id=org_id,
        )

        if not threat:
            return None

        ip = _get_client_ip(request)
        self._log_event(threat, request, ip, user_id, org_id)
        self._increment_threat_counter(ip)
        return JsonResponse(
            {'detail': 'Forbidden — security policy violation.'},
            status=403,
        )

    @staticmethod
    def _log_event(threat, request, ip: str, user_id: str, org_id: str) -> None:
        try:
            from apps.security.models import ApiSecurityEvent
            ApiSecurityEvent.objects.create(
                event_type=threat.event_type,
                severity=threat.severity,
                ip_address=ip or None,
                user_id=user_id,
                organization_id=org_id,
                endpoint=request.path_info[:255],
                http_method=request.method,
                threat_detail=threat.detail[:1000],
                request_id=request.META.get('HTTP_X_REQUEST_ID', '')[:64],
            )
        except Exception as exc:
            logger.error('ApiSecurityMiddleware: failed to write security event: %s', exc)

    @staticmethod
    def _increment_threat_counter(ip: str) -> None:
        if _is_internal_ip(ip):
            return
        try:
            from apps.security.rate_limiter import ConsecutiveAuthFailureLimiter
            ConsecutiveAuthFailureLimiter.record_threat(ip)
        except Exception as exc:
            logger.warning('ApiSecurityMiddleware: rate limiter increment failed: %s', exc)
