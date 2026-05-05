from .base import *
import sentry_sdk

DEBUG = False

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

sentry_sdk.init(
    dsn=env('SENTRY_DSN', default=''),
    traces_sample_rate=0.2,
)