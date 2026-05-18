from rest_framework import serializers
from .models import Property, PropertyImage
from apps.users.models import User


class PropertyImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model  = PropertyImage
        fields = ('id', 'url', 'caption', 'order', 'created_at')

    def get_url(self, obj):
        request = self.context.get('request')
        if not obj.image:
            return None
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url


class PropertyListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model  = Property
        fields = ('id', 'ref_no', 'title', 'city', 'location', 'area_marla', 'price_pkr',
                  'country', 'currency',
                  'property_type', 'construction_status', 'furnished_status',
                  'legal_status', 'ai_score', 'risk_level', 'primary_image', 'created_at')

    def get_primary_image(self, obj):
        first = obj.images.first()
        if not first:
            return None
        request = self.context.get('request')
        url = first.image.url
        return request.build_absolute_uri(url) if request else url


class PropertyDetailSerializer(serializers.ModelSerializer):
    owner_phone   = serializers.CharField(source='owner.phone', read_only=True)
    images        = PropertyImageSerializer(many=True, read_only=True)
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model  = Property
        fields = ('id', 'ref_no', 'owner', 'owner_phone', 'title', 'description',
                  'city', 'location', 'area_marla', 'price_pkr',
                  'country', 'currency',
                  'property_type', 'furnished_status', 'construction_status',
                  'legal_status', 'ai_score', 'risk_level', 'assigned_agent',
                  'installment_available', 'raw_docs', 'ai_analysis',
                  'is_active', 'primary_image', 'images',
                  'created_at', 'updated_at')
        read_only_fields = ('id', 'ref_no', 'owner', 'owner_phone', 'ai_score', 'risk_level',
                            'ai_analysis', 'primary_image', 'images', 'created_at', 'updated_at')

    def get_primary_image(self, obj):
        first = obj.images.first()
        if not first:
            return None
        request = self.context.get('request')
        url = first.image.url
        return request.build_absolute_uri(url) if request else url


class PropertyCreateSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model  = Property
        fields = ('title', 'description', 'city', 'location', 'area_marla',
                  'price_pkr', 'country', 'currency',
                  'property_type', 'furnished_status',
                  'construction_status', 'legal_status', 'assigned_agent', 'owner')

    def validate_price_pkr(self, v):
        if v is not None and v <= 0:
            raise serializers.ValidationError('Price must be positive.')
        return v

    def validate_area_marla(self, v):
        if v is not None and v <= 0:
            raise serializers.ValidationError('Area must be positive.')
        return v