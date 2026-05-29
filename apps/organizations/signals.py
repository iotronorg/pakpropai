from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings


@receiver(post_save, sender='organizations.OrganizationTheme')
def invalidate_theme_cache(sender, instance, **kwargs):
    platform_domain = getattr(settings, 'REALTRON_PLATFORM_DOMAIN', 'realtron.ai')
    org = instance.organization
    keys = [f'theme_cfg:{org.slug}.{platform_domain}']
    if org.custom_domain:
        keys.append(f'theme_cfg:{org.custom_domain}')
    cache.delete_many(keys)
