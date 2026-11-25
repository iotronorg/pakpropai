import time
import logging
from django.conf import settings
from django.db import connection
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseForbidden
from rest_framework.permissions import AllowAny, IsAuthenticated
from .permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """
    GET /health/
    Returns connectivity status for all local services.
    No auth required — used by docker-compose, load balancers, and dev startup checks.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        checks = {}
        overall_ok = True

        # ── Database ──────────────────────────────────────────────────────────
        try:
            t = time.monotonic()
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
            checks['database'] = {'status': 'ok', 'latency_ms': round((time.monotonic() - t) * 1000)}
        except Exception as exc:
            checks['database'] = {'status': 'error', 'detail': str(exc)}
            overall_ok = False

        # ── Redis / Cache ─────────────────────────────────────────────────────
        try:
            t = time.monotonic()
            cache.set('__health_check__', 'ok', 10)
            val = cache.get('__health_check__')
            if val != 'ok':
                raise RuntimeError('cache read/write mismatch')
            checks['redis'] = {'status': 'ok', 'latency_ms': round((time.monotonic() - t) * 1000)}
        except Exception as exc:
            checks['redis'] = {'status': 'error', 'detail': str(exc)}
            overall_ok = False

        # ── Celery (broker reachability via Redis — fast path) ────────────────
        # If Redis is up, the Celery broker is reachable. A full worker ping
        # would add 2s+ latency; we check the broker only here.
        checks['celery_broker'] = (
            {'status': 'ok', 'note': 'broker reachable (Redis ok)'}
            if checks['redis']['status'] == 'ok'
            else {'status': 'error', 'note': 'broker unreachable (Redis down)'}
        )

        # ── AI Backend ───────────────────────────────────────────────────────
        ai_backend = getattr(settings, 'AI_BACKEND', 'gemini')
        if ai_backend == 'gemini':
            key_set = bool(getattr(settings, 'GEMINI_API_KEY', ''))
            checks['ai'] = {
                'status':  'ok' if key_set else 'warning',
                'backend': 'gemini',
                'model':   getattr(settings, 'GEMINI_MODEL', ''),
                'api_key': 'configured' if key_set else 'missing — set GEMINI_API_KEY or switch AI_BACKEND=local',
            }
            if not key_set:
                overall_ok = False
        else:
            ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            try:
                import urllib.request
                t = time.monotonic()
                urllib.request.urlopen(f'{ollama_url}/api/tags', timeout=2)
                checks['ai'] = {
                    'status':  'ok',
                    'backend': 'ollama',
                    'url':     ollama_url,
                    'latency_ms': round((time.monotonic() - t) * 1000),
                }
            except Exception as exc:
                checks['ai'] = {
                    'status':  'error',
                    'backend': 'ollama',
                    'url':     ollama_url,
                    'detail':  str(exc),
                    'hint':    'Run: ollama serve',
                }
                overall_ok = False

        # ── WhatsApp ──────────────────────────────────────────────────────────
        wa_token_set = bool(getattr(settings, 'WA_ACCESS_TOKEN', ''))
        checks['whatsapp'] = {
            'status':      'ok' if wa_token_set else 'warning',
            'access_token': 'configured' if wa_token_set else 'missing — WhatsApp sending disabled',
            'verify_token': 'configured' if getattr(settings, 'WA_VERIFY_TOKEN', '') else 'missing',
        }

        status_code = 200 if overall_ok else 503
        return Response(
            {
                'status': 'ok' if overall_ok else 'degraded',
                'checks': checks,
            },
            status=status_code,
        )


class AuditLogView(APIView):
    """
    GET /api/v1/audit-log/
    Returns paginated audit log entries. Admin-only.

    Query params:
      actor=<uuid>         — filter by actor user ID
      action=<action>      — filter by action type
      target_model=<name>  — filter by model (e.g. User, Lead)
      target_id=<id>       — filter by target pk
      limit=<n>            — page size (default 50, max 200)
      offset=<n>           — offset for pagination
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        from .models import AuditLog

        qs = AuditLog.objects.select_related('actor').order_by('-created_at')

        if actor := request.query_params.get('actor'):
            qs = qs.filter(actor__phone__icontains=actor)
        if action := request.query_params.get('action'):
            qs = qs.filter(action=action)
        if model := request.query_params.get('target_model'):
            qs = qs.filter(target_model__iexact=model)
        if target_id := request.query_params.get('target_id'):
            qs = qs.filter(target_id=target_id)

        from apps.core.query_params import limit_offset
        limit, offset = limit_offset(request.query_params, default=50, maximum=200)
        total  = qs.count()
        page   = qs[offset: offset + limit]

        results = [
            {
                'id':           entry.id,
                'actor_phone':  entry.actor.phone if entry.actor else None,
                'action':       entry.action,
                'target_model': entry.target_model,
                'target_id':    entry.target_id,
                'detail':       entry.detail,
                'before':       entry.before,
                'after':        entry.after,
                'ip_address':   str(entry.ip_address) if entry.ip_address else None,
                'created_at':   entry.created_at.isoformat(),
            }
            for entry in page
        ]

        return Response({
            'count':   total,
            'limit':   limit,
            'offset':  offset,
            'results': results,
        })


def _scraper_allowed(remote_ip: str, allowed) -> bool:
    """Exact addresses or CIDR ranges (``10.0.0.0/8``); ``0.0.0.0`` allows any (dev/Docker only)."""
    import ipaddress
    if '0.0.0.0' in allowed:
        return True
    try:
        ip = ipaddress.ip_address(remote_ip)
    except ValueError:
        return False
    for entry in allowed:
        try:
            if ip in ipaddress.ip_network(entry.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def _scraper_token_ok(request) -> bool:
    """``Authorization: Bearer <PROMETHEUS_METRICS_TOKEN>`` (a monitoring stack on another host)."""
    import hmac
    token = getattr(settings, 'PROMETHEUS_METRICS_TOKEN', '') or ''
    if len(token) < 16:                       # unset or too short to be a secret: tokens are off
        return False
    scheme, _, given = request.META.get('HTTP_AUTHORIZATION', '').partition(' ')
    return scheme.lower() == 'bearer' and hmac.compare_digest(given.strip().encode(), token.encode())


def metrics_view(request):
    """
    GET /metrics/
    Prometheus metrics endpoint — restricted to internal IPs only.
    Set PROMETHEUS_ALLOWED_IPS in env (addresses or CIDR ranges) to control scraper access,
    and/or PROMETHEUS_METRICS_TOKEN (≥ 16 characters) for a bearer token.
    Set PROMETHEUS_ALLOWED_IPS=0.0.0.0 to allow any IP (dev/Docker only).
    With PROMETHEUS_MULTIPROC_DIR set, the samples of every web process are added up;
    the outbox backlog is read from the database (ADR-050).
    """
    allowed = getattr(settings, 'PROMETHEUS_ALLOWED_IPS', ['127.0.0.1', '::1'])
    if not (_scraper_allowed(request.META.get('REMOTE_ADDR', ''), allowed) or _scraper_token_ok(request)):
        return HttpResponseForbidden('Forbidden')

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from apps.core.metrics import metrics_registry
    from apps.core.slo import outbox_backlog
    return HttpResponse(generate_latest(metrics_registry(outbox_backlog)), content_type=CONTENT_TYPE_LATEST)
