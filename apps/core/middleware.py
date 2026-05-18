import logging
import time

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger('api.audit')

_LAST_ACTIVE_TTL = 300  # seconds — throttle DB writes to once per 5 min per user


class LastActiveMiddleware:
    """
    Updates User.last_active on each authenticated API request, throttled to once
    every 5 minutes via Redis to avoid a DB write on every single request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        self._touch(request)
        return response

    @staticmethod
    def _touch(request):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return
        key = f'last_active:{user.pk}'
        if cache.get(key):
            return
        try:
            user.last_active = timezone.now()
            user.save(update_fields=['last_active'])
            cache.set(key, 1, _LAST_ACTIVE_TTL)
        except Exception:
            pass  # never block the response


class TenantIsolationMiddleware:
    """
    Attaches tenant context to every authenticated request from agent/developer users.

    Sets:
      request.agent_profile  — the user's Agent record (or None)
      request.organization   — the Organization this user controls or belongs to:
                               • developer → user.owned_organization
                               • agent     → agent_profile.organization (None if freelance)

    Non-blocking: never raises. Misconfigurations are logged as warnings so admins
    can detect users with role=agent|developer but no linked record.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._attach_tenant(request)
        return self.get_response(request)

    @staticmethod
    def _attach_tenant(request):
        request.agent_profile = None
        request.organization  = None

        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return

        role = getattr(user, 'role', None)
        if role not in ('agent', 'developer'):
            return

        if role == 'developer':
            try:
                request.organization = user.owned_organization
            except Exception:
                logger.warning(
                    "TenantIsolationMiddleware: user %s has role=developer but no owned_organization.",
                    user.id,
                )
            return

        # role == 'agent'
        try:
            profile = user.agent_profile
            request.agent_profile = profile
            request.organization  = profile.organization   # None if freelance — valid
        except Exception:
            logger.warning(
                "TenantIsolationMiddleware: user %s has role=agent but no agent_profile. "
                "Link an Agent record via admin to restore data access.",
                user.id,
            )


class RequestAuditMiddleware:
    """
    Logs every API request as a structured audit entry: method, path, user,
    HTTP status, and wall-clock duration. Uses a dedicated 'api.audit' logger
    so the output can be routed to a separate log file or external sink.
    Only fires for /api/ paths to avoid polluting with static/admin noise.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/api/'):
            return self.get_response(request)

        t0 = time.monotonic()
        response = self.get_response(request)
        duration_ms = int((time.monotonic() - t0) * 1000)

        user = getattr(request, 'user', None)
        uid  = str(user.pk) if user and user.is_authenticated else 'anon'
        role = getattr(user, 'role', '-') if user and user.is_authenticated else '-'

        audit_logger.info(
            '%s %s user=%s role=%s status=%d %dms',
            request.method,
            request.path,
            uid,
            role,
            response.status_code,
            duration_ms,
        )
        return response
