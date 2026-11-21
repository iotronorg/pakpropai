from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'apps.core'
    label = 'core'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import checks  # noqa: F401  (registers system checks)
        from . import openapi  # noqa: F401  (registers OpenAPI auth extensions)
