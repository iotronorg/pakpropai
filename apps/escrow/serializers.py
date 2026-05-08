from rest_framework import serializers
from .models import EscrowDeal

MIN_TOKEN = 25_000
MAX_TOKEN = 100_000


class EscrowDealSerializer(serializers.ModelSerializer):
    property_title = serializers.CharField(source='property.title',   read_only=True)
    property_city  = serializers.CharField(source='property.city',    read_only=True)
    buyer_phone    = serializers.CharField(source='buyer.phone',      read_only=True)
    seller_phone   = serializers.CharField(source='seller.phone',     read_only=True, allow_null=True, default=None)
    agent_name     = serializers.CharField(source='agent.name',       read_only=True, allow_null=True, default=None)
    hours_remaining = serializers.SerializerMethodField()

    class Meta:
        model = EscrowDeal
        fields = (
            'id', 'property', 'property_title', 'property_city',
            'buyer_phone', 'seller_phone', 'agent_name',
            'token_amount', 'status', 'payment_gateway', 'payment_ref',
            'initiated_via', 'buyer_confirmed', 'seller_confirmed',
            'lock_started_at', 'lock_expires_at', 'hours_remaining',
            'admin_notes', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'property_title', 'property_city', 'buyer_phone', 'seller_phone',
            'agent_name', 'hours_remaining', 'lock_started_at', 'lock_expires_at',
            'buyer_confirmed', 'seller_confirmed', 'created_at', 'updated_at',
        )

    def get_hours_remaining(self, obj):
        return obj.hours_remaining()


class InitiateDealLockSerializer(serializers.Serializer):
    property_id  = serializers.UUIDField()
    token_amount = serializers.IntegerField(min_value=MIN_TOKEN, max_value=MAX_TOKEN)
    payment_gateway = serializers.ChoiceField(
        choices=EscrowDeal.Gateway.choices,
        default=EscrowDeal.Gateway.JAZZCASH,
    )

    def validate_property_id(self, value):
        from apps.properties.models import Property
        try:
            prop = Property.objects.get(id=value, is_active=True)
        except Property.DoesNotExist:
            raise serializers.ValidationError("Property not found or inactive.")
        # Only one active lock per property at a time
        if EscrowDeal.objects.filter(
            property=prop,
            status__in=[EscrowDeal.Status.INITIATED, EscrowDeal.Status.LOCKED]
        ).exists():
            raise serializers.ValidationError(
                "This property already has an active deal lock. Try again after it expires."
            )
        self.context['property'] = prop
        return value


class ConfirmPaymentSerializer(serializers.Serializer):
    payment_ref     = serializers.CharField(max_length=200)
    payment_gateway = serializers.ChoiceField(choices=EscrowDeal.Gateway.choices, required=False)
    admin_notes     = serializers.CharField(required=False, allow_blank=True)
