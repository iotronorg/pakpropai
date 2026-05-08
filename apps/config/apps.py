from django.apps import AppConfig


class ConfigApp(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.config'
    label = 'sysconfig'
    verbose_name = 'System Configuration'
