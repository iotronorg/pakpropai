"""
Local end-to-end run (scripts/dev/e2e.sh): production-like, so DEBUG is off and
tasks go through a real worker. Only what would need HTTPS or a key store differs.
"""
from .base import *

DEBUG = False
ALLOWED_HOSTS = ['*']
FIELD_ENCRYPTION_ALLOW_DERIVED_KEY = True
CELERY_TASK_ALWAYS_EAGER = False
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
# One address plays every person.
REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'].update({'otp_send': '60/hour', 'otp_daily': '200/day', 'anon': '300/min',
                                                 'ai_query': '60/min'})
