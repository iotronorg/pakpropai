import logging
import time

from django.core.cache import cache
from django.http import JsonResponse
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


class TraceContextMiddleware:
    """
    Populates per-request organization_id and lead_id context vars so that every
    log line emitted during this request automatically carries those identifiers.

    Must be placed AFTER TenantIsolationMiddleware (which resolves
    request.organization) and BEFORE RequestAuditMiddleware (so audit log lines
    are already tagged).

    Views that identify a lead should call:
        from apps.core.context import set_trace_context
        set_trace_context(lead_id=str(lead.pk))
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.core.context import set_trace_context, clear_trace_context

        org = getattr(request, 'organization', None)
        set_trace_context(organization_id=str(org.pk) if org else '')
        try:
            return self.get_response(request)
        finally:
            clear_trace_context()


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


class TenantDomainMiddleware:
    """
    Resolves the incoming Host header to an Organization and stores it as
    request.tenant_org for consumption by API key auth and external views.

    Resolution order:
      1. Redis cache  (TTL=300 s) — target < 2 ms overhead on hot paths
      2. DB lookup    — by org slug (subdomain) or custom_domain (FQDN)

    Subdomain pattern  : {slug}.realtron.ai  → Organization.slug
    Custom domain      : portal.imarat.ai    → Organization.custom_domain

    Unrecognized *custom* domains return 404 immediately; unrecognized
    subdomains (e.g. typos) pass through so Django URL routing handles them.
    Platform own domain and local dev hostnames are skipped silently.

    Performance: a Redis GET adds ≈ 1–2 ms; DB fallback ≈ 3–8 ms.
    Both are safely within the 12 ms SLA defined in the architecture spec.
    """

    _CACHE_TTL    = 300   # seconds
    _SENTINEL     = '__none__'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        short_circuit = self._resolve(request)
        if short_circuit is not None:
            return short_circuit
        return self.get_response(request)

    def _resolve(self, request) -> JsonResponse | None:
        """
        Populates request.tenant_org.
        Returns a 404 JsonResponse only for unrecognized custom domains.
        """
        request.tenant_org = None

        from django.conf import settings
        host = request.get_host().split(':')[0].lower()
        platform_domain = getattr(settings, 'REALTRON_PLATFORM_DOMAIN', 'realtron.ai')

        # Skip resolution for the platform's own origin and local dev environments
        if host in (platform_domain, 'localhost', '127.0.0.1', 'testserver'):
            return None

        is_subdomain = host.endswith(f'.{platform_domain}')
        org = self._lookup(host, platform_domain, is_subdomain)

        if org is None and not is_subdomain:
            # Unrecognized custom domain — hard 404
            logger.info('TenantDomainMiddleware: unrecognized custom domain %s', host)
            return JsonResponse({'detail': 'Domain not recognized.'}, status=404)

        request.tenant_org = org
        return None

    def _lookup(self, host: str, platform_domain: str, is_subdomain: bool):
        """Cache-first DB lookup. Returns Organization or None."""
        from apps.organizations.models import Organization

        cache_key = f'tenant_domain:{host}'
        cached = cache.get(cache_key)

        if cached is not None:
            if cached == self._SENTINEL:
                return None
            try:
                return Organization.objects.only(
                    'id', 'name', 'slug', 'custom_domain',
                    'is_active', 'plan', 'country', 'measurement_system',
                ).get(pk=cached)
            except Organization.DoesNotExist:
                cache.delete(cache_key)

        if is_subdomain:
            slug = host[: -(len(platform_domain) + 1)]
            org = (
                Organization.objects
                .filter(slug=slug, is_active=True)
                .only('id', 'name', 'slug', 'custom_domain', 'is_active', 'plan', 'country', 'measurement_system')
                .first()
            )
        else:
            org = (
                Organization.objects
                .filter(custom_domain=host, is_active=True)
                .only('id', 'name', 'slug', 'custom_domain', 'is_active', 'plan', 'country', 'measurement_system')
                .first()
            )

        cache.set(cache_key, str(org.pk) if org else self._SENTINEL, self._CACHE_TTL)
        return org
