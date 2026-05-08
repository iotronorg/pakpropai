import time
import logging
from django.conf import settings
from django.db import connection
from django.core.cache import cache
from rest_framework.permissions import AllowAny
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
