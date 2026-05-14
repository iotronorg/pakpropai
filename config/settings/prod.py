from .base import *

DEBUG = False

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

# ── HTTPS / SSL ────────────────────────────────────────────────────────────────
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True

# ── HSTS ──────────────────────────────────────────────────────────────────────
# 1-year duration. Only enable once you are certain the domain is HTTPS-only.
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# ── Cookie security ───────────────────────────────────────────────────────────
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# ── Security headers ──────────────────────────────────────────────────────────
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# ── CORS + CSRF: restrict to production frontend only ─────────────────────────
# Set FRONTEND_URL in .env — e.g. https://app.pakpropai.com
CORS_ALLOWED_ORIGINS  = [env('FRONTEND_URL')]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS  = [env('FRONTEND_URL')]

# Sentry is initialized in base.py with Django/Celery/Redis integrations.
# Set SENTRY_DSN in the environment to enable it.
