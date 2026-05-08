import logging

from django.core.cache import cache

from .models import SystemConfig

logger = logging.getLogger(__name__)

CACHE_PREFIX = 'sysconfig:'
CACHE_TTL    = 60  # seconds — short enough to pick up admin changes quickly
SENTINEL     = '__configured__'


class SystemConfigService:

    @classmethod
    def get(cls, key: str, default: str = '') -> str:
        """
        Priority: Redis cache → DB → Django settings (env) → DEFAULTS → default arg.
        Late import of `settings` to avoid circular imports at startup.
        """
        cached = cache.get(f'{CACHE_PREFIX}{key}')
        if cached is not None:
            return cached

        try:
            obj = SystemConfig.objects.get(key=key)
            value = obj.value
            cache.set(f'{CACHE_PREFIX}{key}', value, CACHE_TTL)
            return value
        except SystemConfig.DoesNotExist:
            pass

        env_attr = SystemConfig.ENV_KEY_MAP.get(key)
        if env_attr:
            try:
                from django.conf import settings as _s
                value = getattr(_s, env_attr, '') or ''
                if value:
                    cache.set(f'{CACHE_PREFIX}{key}', value, CACHE_TTL)
                    return value
            except Exception:
                pass

        return SystemConfig.DEFAULTS.get(key, default)

    @classmethod
    def is_set(cls, key: str) -> bool:
        """True if a non-empty value exists in DB or ENV (not just DEFAULTS)."""
        try:
            obj = SystemConfig.objects.get(key=key)
            if obj.value:
                return True
        except SystemConfig.DoesNotExist:
            pass
        env_attr = SystemConfig.ENV_KEY_MAP.get(key)
        if env_attr:
            try:
                from django.conf import settings as _s
                return bool(getattr(_s, env_attr, ''))
            except Exception:
                pass
        return False

    @classmethod
    def set(cls, key: str, value: str, user=None):
        SystemConfig.objects.update_or_create(
            key=key,
            defaults={'value': value, 'updated_by': user},
        )
        cache.delete(f'{CACHE_PREFIX}{key}')

    @classmethod
    def bulk_set(cls, data: dict, user=None):
        for key, value in data.items():
            if key not in SystemConfig.DEFAULTS:
                continue
            SystemConfig.objects.update_or_create(
                key=key,
                defaults={'value': value, 'updated_by': user},
            )
            cache.delete(f'{CACHE_PREFIX}{key}')

    @classmethod
    def get_all(cls) -> dict:
        """Returns merged dict: DEFAULTS ← DB rows (DB wins)."""
        result = dict(SystemConfig.DEFAULTS)
        for obj in SystemConfig.objects.all():
            if obj.key in result:
                result[obj.key] = obj.value
        return result

    @classmethod
    def get_missing_required(cls) -> list[str]:
        return [k for k in SystemConfig.REQUIRED_KEYS if not cls.is_set(k)]

    @classmethod
    def get_features(cls) -> dict[str, bool]:
        """Returns {feature_key: bool} for all feature_* config keys."""
        features = {}
        for key in SystemConfig.DEFAULTS:
            if key.startswith('feature_'):
                features[key] = cls.get(key, 'true') == 'true'
        return features

    @classmethod
    def scraper_enabled(cls) -> bool:
        return cls.get('scraper_search_enabled', 'true') == 'true'

    @classmethod
    def get_active_gateway(cls) -> str:
        return cls.get('active_payment_gateway', 'manual')
