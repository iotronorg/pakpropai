import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_celery_results',
    'django_celery_beat',
]

LOCAL_APPS = [
    'apps.core',
    'apps.config',
    'apps.users',
    'apps.properties',
    'apps.ai',
    'apps.payments',
    'apps.notifications',
    'apps.whatsapp',
    'apps.verification',
    'apps.leads',
    'apps.escrow',
    'apps.reports',
    'apps.audit',
    'apps.agents',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': env.db('DATABASE_URL')
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Karachi'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'users.User'

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'apps.users.authentication.JWTCookieOrHeaderAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'anon': '30/min',
        'user': '120/min',
        'otp_send': '3/hour',
        'ai_query': '10/min',
        'fraud_check': '20/min',
    },
}

from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ALGORITHM': 'HS256',
    'AUTH_HEADER_TYPES': ('Bearer',),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# Redis / Celery
REDIS_URL = env('REDIS_URL', default='redis://localhost:6379/0')
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = 'django-db'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Karachi'

CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

CELERY_BEAT_SCHEDULE = {
    'expire-deal-locks': {
        'task':     'apps.escrow.tasks.expire_deal_locks',
        'schedule': 1800,  # every 30 minutes
    },
    'nightly-property-rescore': {
        'task':     'apps.properties.tasks.rescore_all_properties_task',
        'schedule': 86400,  # every 24 hours
    },
    'daily-stale-lead-detection': {
        'task':     'apps.leads.tasks.mark_stale_leads',
        'schedule': 86400,  # every 24 hours
    },
    'daily-stale-lead-reminders': {
        'task':     'apps.leads.tasks.send_stale_lead_reminders',
        'schedule': 86400,  # every 24 hours
    },
    'daily-agent-performance-snapshot': {
        'task':     'apps.agents.tasks.refresh_agent_performance_snapshots',
        'schedule': 86400,  # every 24 hours
    },
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
    }
}

# WhatsApp
WA_VERIFY_TOKEN    = env('WA_VERIFY_TOKEN', default='')
WA_APP_SECRET      = env('WA_APP_SECRET', default='')
WA_ACCESS_TOKEN    = env('WA_ACCESS_TOKEN', default='')
WA_PHONE_NUMBER_ID = env('WA_PHONE_NUMBER_ID', default='')
# Pre-approved Meta template for OTP (required for users outside 24-h session window).
# Create in Meta Business Manager → WhatsApp → Message Templates.
# Body example: "Your PakProp AI code is {{1}}. Expires in 5 minutes."
WA_OTP_TEMPLATE_NAME = env('WA_OTP_TEMPLATE_NAME', default='')

# Gemini
GEMINI_API_KEY = env('GEMINI_API_KEY', default='')
GEMINI_MODEL   = env('GEMINI_MODEL',   default='gemini-2.5-flash-lite')

# AI backend switcher: 'gemini' (cloud) or 'local' (Ollama)
AI_BACKEND         = env('AI_BACKEND',         default='gemini').strip().lower()
LOCAL_MODEL        = env('LOCAL_MODEL',        default='qwen2.5:7b')
LOCAL_VISION_MODEL = env('LOCAL_VISION_MODEL', default='llava:7b')
OLLAMA_BASE_URL    = env('OLLAMA_BASE_URL',    default='http://localhost:11434')

# Media files (uploaded docs, generated PDFs)
MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Base URL for generating absolute links (PDF download, etc.)
BASE_URL = env('BASE_URL', default='http://127.0.0.1:8000')

# Safepay (primary online payment gateway)
# Sign up at: https://getsafepay.com
# Dashboard → Settings → API Keys
SAFEPAY_MERCHANT_KEY  = env('SAFEPAY_MERCHANT_KEY',  default='')
SAFEPAY_SECRET_KEY    = env('SAFEPAY_SECRET_KEY',    default='')
SAFEPAY_ENVIRONMENT   = env('SAFEPAY_ENVIRONMENT',   default='sandbox')  # 'sandbox' | 'production'

# bSecure (secondary online payment gateway)
# Sign up at: https://bsecure.pk
BSECURE_CLIENT_ID     = env('BSECURE_CLIENT_ID',     default='')
BSECURE_CLIENT_SECRET = env('BSECURE_CLIENT_SECRET', default='')
BSECURE_ENVIRONMENT   = env('BSECURE_ENVIRONMENT',   default='sandbox')  # 'sandbox' | 'production'