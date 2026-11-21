from django.apps import AppConfig


class MarketsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.markets'
    label = 'markets'

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from .models import Market
        from .services import invalidate

        def _invalidate(sender, instance, **kwargs):
            invalidate(instance.code)

        post_save.connect(_invalidate, sender=Market, dispatch_uid='market-cache-save')
        post_delete.connect(_invalidate, sender=Market, dispatch_uid='market-cache-delete')
