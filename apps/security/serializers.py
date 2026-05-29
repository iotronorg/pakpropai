from rest_framework import serializers
from apps.security.models import ApiSecurityEvent


class ApiSecurityEventSerializer(serializers.ModelSerializer):
    severity_display   = serializers.CharField(source='get_severity_display',   read_only=True)
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)

    class Meta:
        model  = ApiSecurityEvent
        fields = [
            'id', 'event_type', 'event_type_display', 'severity', 'severity_display',
            'ip_address', 'user_id', 'organization_id', 'endpoint', 'http_method',
            'threat_detail', 'request_id', 'record_hash', 'created_at',
        ]
        read_only_fields = fields
