from rest_framework import serializers
from .models import OrgWhatsAppConfig


class OrgWhatsAppConfigSerializer(serializers.ModelSerializer):
    _SECRET_KEYS = OrgWhatsAppConfig._SECRET_KEYS

    class Meta:
        model  = OrgWhatsAppConfig
        exclude = ['id', 'organization', 'created_at', 'updated_at']
        read_only_fields = ['webhook_verified_at', 'meta_profile_synced_at']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for key in self._SECRET_KEYS:
            if data.get(key):
                data[key] = '••••••••'
        return data

    def validate(self, attrs):
        # If a secret field arrives as '••••••••', pop it so the existing
        # DB value is preserved rather than overwritten with the mask string.
        for key in self._SECRET_KEYS:
            if attrs.get(key) == '••••••••':
                attrs.pop(key)
        return attrs
