from .dev import *

# Debug Toolbar is incompatible with the Django test runner — strip it out.
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != 'debug_toolbar']
MIDDLEWARE     = [m   for m   in MIDDLEWARE     if 'debug_toolbar' not in m]

# Celery tasks run synchronously and errors propagate so assertions work correctly.
CELERY_TASK_ALWAYS_EAGER    = True
CELERY_TASK_EAGER_PROPAGATES = True
