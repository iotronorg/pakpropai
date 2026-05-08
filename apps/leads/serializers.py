from rest_framework import serializers
from .models import Lead, Appointment


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


class AppointmentSerializer(serializers.ModelSerializer):
    lead_phone      = serializers.CharField(source='lead.user.phone', read_only=True)
    agent_name      = serializers.CharField(source='agent.name', read_only=True, allow_null=True)
    property_title  = serializers.CharField(source='property.title', read_only=True, allow_null=True)

    class Meta:
        model  = Appointment
        fields = (
            'id', 'lead', 'lead_phone', 'property', 'property_title',
            'agent', 'agent_name', 'scheduled_at', 'duration_minutes',
            'status', 'notes', 'reminder_sent_at', 'created_by',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'lead_phone', 'agent_name', 'property_title',
                            'reminder_sent_at', 'created_by', 'created_at', 'updated_at')
