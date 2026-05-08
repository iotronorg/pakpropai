from rest_framework import serializers
from .models import Lead


class LeadSerializer(serializers.ModelSerializer):
    phone             = serializers.CharField(source='user.phone', read_only=True)
    name              = serializers.CharField(source='user.name', read_only=True, allow_null=True)
    intent_score      = serializers.IntegerField(source='score', read_only=True)
    location_interest = serializers.CharField(source='city_interest', read_only=True, allow_blank=True)

    class Meta:
        model  = Lead
        fields = (
            'id', 'phone', 'name', 'intent', 'intent_score',
            'location_interest', 'budget_min', 'budget_max',
            'status', 'notes', 'created_at',
        )
        read_only_fields = (
            'id', 'phone', 'name', 'intent', 'intent_score',
            'location_interest', 'budget_min', 'budget_max', 'created_at',
        )
