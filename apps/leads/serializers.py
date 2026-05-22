from rest_framework import serializers
from .models import Lead, LeadActivity, LeadScoreHistory, Appointment, ConversationMessage


class LeadSerializer(serializers.ModelSerializer):
    phone             = serializers.CharField(source='user.phone', read_only=True)
    name              = serializers.CharField(source='user.name', read_only=True, allow_null=True)
    intent_score      = serializers.IntegerField(source='score', read_only=True)
    score_factors     = serializers.JSONField(read_only=True)
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
            'location_interest', 'budget_min', 'budget_max', 'budget_currency',
            'status', 'priority', 'routing_state',
            'notes', 'source', 'intent_signals', 'score_factors',
            'organization',
            'assigned_agent_id', 'assigned_agent_name',
            'last_contacted_at', 'created_at',
        )
        read_only_fields = (
            'id', 'phone', 'name', 'intent', 'intent_score',
            'location_interest', 'budget_min', 'budget_max', 'budget_currency',
            'priority', 'routing_state',
            'source', 'intent_signals', 'score_factors',
            'organization',
            'assigned_agent_id', 'assigned_agent_name',
            'last_contacted_at', 'created_at',
        )


class ConversationMessageSerializer(serializers.ModelSerializer):
    sender_phone = serializers.CharField(source='sender.phone', read_only=True, allow_null=True)
    sender_name  = serializers.CharField(source='sender.name',  read_only=True, allow_null=True)

    class Meta:
        model  = ConversationMessage
        fields = ('id', 'direction', 'channel', 'body',
                  'sender_phone', 'sender_name', 'wa_message_id', 'created_at')


class AppointmentSerializer(serializers.ModelSerializer):
    lead_phone      = serializers.CharField(source='lead.user.phone', read_only=True)
    lead_name       = serializers.CharField(source='lead.user.name', read_only=True, allow_null=True)
    agent_name      = serializers.CharField(source='agent.name', read_only=True, allow_null=True)
    property_title  = serializers.CharField(source='property.title', read_only=True, allow_null=True)

    class Meta:
        model  = Appointment
        fields = (
            'id', 'lead', 'lead_phone', 'lead_name', 'property', 'property_title',
            'agent', 'agent_name', 'scheduled_at', 'duration_minutes',
            'status', 'notes', 'reminder_sent_at', 'created_by',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'lead_phone', 'lead_name', 'agent_name', 'property_title',
                            'reminder_sent_at', 'created_by', 'created_at', 'updated_at')


class LeadActivitySerializer(serializers.ModelSerializer):
    actor_phone = serializers.CharField(source='actor.phone', read_only=True, allow_null=True)
    actor_name  = serializers.CharField(source='actor.name',  read_only=True, allow_null=True)

    class Meta:
        model  = LeadActivity
        fields = ('id', 'action', 'notes', 'meta', 'actor_phone', 'actor_name', 'created_at')


class LeadScoreHistorySerializer(serializers.ModelSerializer):
    changed_by_phone = serializers.CharField(source='changed_by.phone', read_only=True, allow_null=True)

    class Meta:
        model  = LeadScoreHistory
        fields = ('id', 'old_score', 'new_score', 'reason', 'changed_by_phone', 'created_at')
