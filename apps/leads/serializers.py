from rest_framework import serializers
from .models import Lead


class LeadSerializer(serializers.ModelSerializer):
    phone             = serializers.CharField(source='user.phone', read_only=True)
    name              = serializers.CharField(source='user.name', read_only=True, allow_null=True)
    intent_score      = serializers.IntegerField(source='score', read_only=True)
    location_interest = serializers.CharField(source='city_interest', read_only=True, allow_blank=True)
    assigned_agent_id = serializers.PrimaryKeyRelatedField(
        source='assigned_agent', read_only=True
    )
    assigned_agent_name = serializers.CharField(
        source='assigned_agent.name', read_only=True, allow_null=True, default=None
    )

    class Meta:
        model  = Lead
        fields = (
            'id', 'phone', 'name', 'intent', 'intent_score',
            'location_interest', 'budget_min', 'budget_max',
            'status', 'notes', 'assigned_agent_id', 'assigned_agent_name',
            'created_at',
        )
        read_only_fields = (
            'id', 'phone', 'name', 'intent', 'intent_score',
            'location_interest', 'budget_min', 'budget_max',
            'assigned_agent_id', 'assigned_agent_name', 'created_at',
        )
