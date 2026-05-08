from rest_framework import serializers
from .models import Property


class PropertyListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""
    class Meta:
        model  = Property
        fields = ('id', 'title', 'city', 'location', 'area_marla', 'price_pkr',
                  'property_type', 'construction_status', 'furnished_status',
                  'legal_status', 'ai_score', 'risk_level', 'created_at')


class PropertyDetailSerializer(serializers.ModelSerializer):
    owner_phone = serializers.CharField(source='owner.phone', read_only=True)

    class Meta:
        model  = Property
        fields = ('id', 'owner', 'owner_phone', 'title', 'description',
                  'city', 'location', 'area_marla', 'price_pkr',
                  'property_type', 'legal_status', 'ai_score', 'risk_level',
                  'raw_docs', 'ai_analysis', 'is_active',
                  'created_at', 'updated_at')
        read_only_fields = ('id', 'owner', 'owner_phone', 'ai_score', 'risk_level',
                            'ai_analysis', 'created_at', 'updated_at')


class PropertyCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Property
        fields = ('title', 'description', 'city', 'location', 'area_marla',
                  'price_pkr', 'property_type')

    def validate_price_pkr(self, v):
        if v is not None and v <= 0:
            raise serializers.ValidationError('Price must be positive.')
        return v

    def validate_area_marla(self, v):
        if v is not None and v <= 0:
            raise serializers.ValidationError('Area must be positive.')
        return v