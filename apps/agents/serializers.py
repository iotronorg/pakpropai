from rest_framework import serializers
from .models import Agent


class AgentSerializer(serializers.ModelSerializer):
    user_phone = serializers.CharField(source='user.phone', read_only=True, allow_null=True)
    user_email = serializers.CharField(source='user.email', read_only=True, allow_null=True)
    parent_organization_name = serializers.CharField(
        source='parent_organization.name', read_only=True, allow_null=True, default=None
    )

    class Meta:
        model = Agent
        fields = (
            'id', 'name', 'agent_type', 'phone', 'whatsapp_number', 'email',
            'company_name', 'designation', 'bio',
            'specializations', 'cities', 'areas', 'primary_city',
            'is_verified', 'is_active', 'is_featured',
            'total_leads', 'total_listings', 'closed_deals', 'rating',
            'user_phone', 'user_email',
            'parent_organization', 'parent_organization_name',
            'joined_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'is_verified', 'total_leads', 'total_listings',
            'closed_deals', 'rating', 'user_phone', 'user_email',
            'parent_organization_name', 'joined_at', 'updated_at',
        )
