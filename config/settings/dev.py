from .base import *

DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS += ['debug_toolbar']
MIDDLEWARE += ['debug_toolbar.middleware.DebugToolbarMiddleware']

INTERNAL_IPS = ['127.0.0.1']

DEBUG_TOOLBAR_CONFIG = {'IS_RUNNING_TESTS': False}

# Override DB to use local SQLite during initial setup if you don't have Supabase yet
# Comment this out once you have DATABASE_URL in .env
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

CORS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
]

# Run Celery tasks synchronously inline — no worker process needed in dev.
# EAGER_PROPAGATES is False so WhatsApp/delivery failures don't crash the request.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False

# Relaxed throttle limits in dev — avoids lockouts during testing.
REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'].update({
    'otp_send':  '60/hour',
    'otp_daily': '200/day',
})