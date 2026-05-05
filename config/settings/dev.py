from .base import *

DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS += ['debug_toolbar']
MIDDLEWARE += ['debug_toolbar.middleware.DebugToolbarMiddleware']

INTERNAL_IPS = ['127.0.0.1']

# Override DB to use local SQLite during initial setup if you don't have Supabase yet
# Comment this out once you have DATABASE_URL in .env
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'