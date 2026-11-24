from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.properties.models import ListingTransition


@receiver(post_save, sender=ListingTransition, dispatch_uid='moderation_on_transition')
def _on_transition(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from .services import on_transition
        on_transition(instance)
