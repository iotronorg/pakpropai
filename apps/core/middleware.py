import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

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
      request.tenant_org     — the org this user belongs to / controls:
                               • developer → their own agent profile (they ARE the org)
                               • agent     → their parent_organization (None if independent)

    Non-blocking: never raises. Misconfigurations are logged as warnings so admins
    can detect users with role=agent|developer but no linked Agent record.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._attach_tenant(request)
        return self.get_response(request)

    @staticmethod
    def _attach_tenant(request):
        request.agent_profile = None
        request.tenant_org = None

        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return

        role = getattr(user, 'role', None)
        if role not in ('agent', 'developer'):
            return

        try:
            profile = user.agent_profile          # OneToOne reverse accessor
            request.agent_profile = profile

            if role == 'developer':
                request.tenant_org = profile      # developer record IS the org
            else:
                # Independent agents have no parent_org; that is valid.
                request.tenant_org = profile.parent_organization

        except Exception:
            # User has role=agent|developer but no Agent record linked — misconfiguration.
            logger.warning(
                "TenantIsolationMiddleware: user %s has role=%s but no agent_profile. "
                "Link an Agent record via admin to restore data access.",
                user.id, role,
            )
