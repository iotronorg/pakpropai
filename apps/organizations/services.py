"""
OrgConfigService — per-organization feature flag resolution.

Priority: OrganizationConfig (org row) → SystemConfig (platform default) → DEFAULTS.
"""
from django.core.cache import cache

from .models import OrganizationConfig

_CACHE_TTL = 60  # seconds


class OrgConfigService:

    @staticmethod
    def _cache_key(org_id, key: str) -> str:
        return f'orgcfg:{org_id}:{key}'

    @classmethod
    def get(cls, organization, key: str) -> str:
        """
        Resolve a config key for an organization.
        Falls back to platform SystemConfig, then to DEFAULTS.
        """
        cache_key = cls._cache_key(organization.pk, key)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            row = OrganizationConfig.objects.get(organization=organization, key=key)
            value = row.value
            cache.set(cache_key, value, _CACHE_TTL)
            return value
        except OrganizationConfig.DoesNotExist:
            pass

        # Fall back to platform-level config
        from apps.config.services import SystemConfigService
        value = SystemConfigService.get(key)
        cache.set(cache_key, value, _CACHE_TTL)
        return value

    @classmethod
    def is_feature_enabled(cls, organization, key: str) -> bool:
        return cls.get(organization, key) == 'true'

    @classmethod
    def get_features(cls, organization) -> dict:
        """Return {feature_key: bool} for all allowed feature keys."""
        return {
            key: cls.is_feature_enabled(organization, key)
            for key in OrganizationConfig.ALLOWED_KEYS
        }

    @classmethod
    def set(cls, organization, key: str, value: str, user=None):
        if key not in OrganizationConfig.ALLOWED_KEYS:
            raise ValueError(f"'{key}' is not an overridable org config key.")
        OrganizationConfig.objects.update_or_create(
            organization=organization,
            key=key,
            defaults={'value': value, 'updated_by': user},
        )
        cache.delete(cls._cache_key(organization.pk, key))

    @classmethod
    def bulk_set(cls, organization, data: dict, user=None):
        for key, value in data.items():
            cls.set(organization, key, value, user=user)

    @classmethod
    def reset(cls, organization, key: str):
        """Remove org override — reverts to platform default."""
        OrganizationConfig.objects.filter(organization=organization, key=key).delete()
        cache.delete(cls._cache_key(organization.pk, key))
