from rest_framework import serializers
from .models import VoiceCallSession, OrgVoiceConfig

_MASKED = '••••••••'


class VoiceCallSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VoiceCallSession
        fields = (
            'id', 'call_sid', 'from_phone', 'to_phone', 'direction',
            'status', 'barge_in_at', 'transcript', 'recording_url',
            'started_at', 'ended_at', 'duration_seconds', 'created_at',
        )
        read_only_fields = fields


class OrgVoiceConfigSerializer(serializers.ModelSerializer):
    auth_token = serializers.SerializerMethodField()

    class Meta:
        model  = OrgVoiceConfig
        fields = ('account_sid', 'auth_token', 'phone_number',
                  'is_active', 'record_calls', 'ai_voice_name')

    def get_auth_token(self, obj):
        return _MASKED if obj.auth_token else ''

    def update(self, instance, validated_data):
        # Preserve existing auth_token when client sends the masked sentinel
        if validated_data.get('auth_token') == _MASKED:
            validated_data.pop('auth_token')
        return super().update(instance, validated_data)
