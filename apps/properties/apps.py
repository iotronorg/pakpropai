from django.apps import AppConfig


class PropertiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.properties"

    def ready(self):
        from . import signals  # noqa: F401  (T-4050 duplicate clusters)
