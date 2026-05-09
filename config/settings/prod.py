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

# ── CORS: restrict to production frontend only ────────────────────────────────
# Set FRONTEND_URL in .env — e.g. https://app.pakpropai.com
CORS_ALLOWED_ORIGINS = [env('FRONTEND_URL')]
CORS_ALLOW_CREDENTIALS = True

# ── Sentry error monitoring (optional — install sentry-sdk to enable) ─────────
try:
    import sentry_sdk
    _dsn = env('SENTRY_DSN', default='')
    if _dsn:
        sentry_sdk.init(dsn=_dsn, traces_sample_rate=0.2)
except ImportError:
    pass
