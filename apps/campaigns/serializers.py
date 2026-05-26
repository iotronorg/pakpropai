from rest_framework import serializers
from .models import Campaign


class CampaignSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = Campaign
        fields = [
            'id', 'name', 'message_template', 'audience_filter',
            'meta_template_name', 'meta_template_language', 'meta_template_components',
            'messaging_tier', 'budget_min', 'budget_max', 'area_interest',
            'scheduled_at', 'status', 'recipient_count', 'sent_count',
            'failed_count', 'sent_at', 'created_by', 'created_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'recipient_count', 'sent_count', 'failed_count',
            'sent_at', 'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.name or obj.created_by.phone
        return None

    def validate_messaging_tier(self, value):
        if value not in (1, 2, 3):
            raise serializers.ValidationError("messaging_tier must be 1, 2, or 3.")
        return value
