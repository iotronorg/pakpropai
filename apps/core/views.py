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
            qs = qs.filter(actor_id=actor)
        if action := request.query_params.get('action'):
            qs = qs.filter(action=action)
        if model := request.query_params.get('target_model'):
            qs = qs.filter(target_model__iexact=model)
        if target_id := request.query_params.get('target_id'):
            qs = qs.filter(target_id=target_id)

        limit  = min(int(request.query_params.get('limit',  50)), 200)
        offset = max(int(request.query_params.get('offset', 0)),  0)
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


def metrics_view(request):
    """
    GET /metrics/
    Prometheus metrics endpoint — restricted to internal IPs only.
    Set PROMETHEUS_ALLOWED_IPS in env to control scraper access.
    Set PROMETHEUS_ALLOWED_IPS=0.0.0.0 to allow any IP (dev/Docker only).
    """
    allowed = getattr(settings, 'PROMETHEUS_ALLOWED_IPS', ['127.0.0.1', '::1'])
    if '0.0.0.0' not in allowed:
        remote_ip = (
            request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', '')
        )
        if remote_ip not in allowed:
            return HttpResponseForbidden('Forbidden')

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
