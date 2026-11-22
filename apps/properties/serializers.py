from rest_framework import serializers
from .models import PriceHistory, Property, PropertyImage
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


V2_FIELDS = ('purpose', 'bedrooms', 'bathrooms', 'floor', 'total_floors', 'year_built',
             'parking_spaces', 'pets_allowed', 'price_type', 'price_negotiable',
             'price_on_request', 'deposit', 'service_charge', 'attributes')


_DEPRECATED = 'Deprecated (T-1070b): use {0}. Kept in sync until removal (T-1070g).'
_SIZE_EXTRA_KWARGS = {
    'area_marla': {'help_text': _DEPRECATED.format('size_value')},
    'area_unit':  {'help_text': _DEPRECATED.format('size_unit')},
    'area_sqm':   {'help_text': _DEPRECATED.format('size_m2')},
    'size_value': {'help_text': 'Size in size_unit.'},
    'size_m2':    {'help_text': 'Canonical size in m², computed from size_value + size_unit.'},
}


class ListingV2ValidationMixin:
    """Cross-field rules for listing schema v2 (T-1070a/b); partial updates merge the instance."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        for new, old in (('size_value', 'area_marla'), ('size_unit', 'area_unit')):
            if new in attrs and old in attrs and attrs[new] != attrs[old]:
                raise serializers.ValidationError(
                    {old: f'Conflicts with {new}; send only {new} ({old} is deprecated).'})
        merged = {f: getattr(self.instance, f, None) for f in V2_FIELDS} if self.instance else {}
        merged.update({k: v for k, v in attrs.items() if k in V2_FIELDS})
        errors = Property.v2_errors(merged)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class PropertyListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model  = Property
        fields = ('id', 'ref_no', 'title', 'city', 'location',
                  'size_value', 'size_unit', 'size_m2',
                  'area_marla', 'area_unit', 'area_sqm',
                  'price', 'country', 'currency',
                  'purpose', 'price_type', 'price_on_request', 'bedrooms', 'bathrooms',
                  'property_type', 'construction_status', 'furnished_status',
                  'legal_status', 'ai_score', 'risk_level', 'primary_image', 'created_at')
        extra_kwargs = _SIZE_EXTRA_KWARGS

    def get_primary_image(self, obj):
        first = obj.images.first()
        if not first:
            return None
        request = self.context.get('request')
        url = first.image.url
        return request.build_absolute_uri(url) if request else url


class PropertyDetailSerializer(ListingV2ValidationMixin, serializers.ModelSerializer):
    owner_phone   = serializers.CharField(source='owner.phone', read_only=True)
    images        = PropertyImageSerializer(many=True, read_only=True)
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model  = Property
        fields = ('id', 'ref_no', 'owner', 'owner_phone', 'title', 'description',
                  'city', 'location', 'size_value', 'size_unit', 'size_m2', 'plot_size_m2',
                  'area_marla', 'area_unit', 'area_sqm',
                  'price', 'country', 'currency',
                  'property_type', 'furnished_status', 'construction_status',
                  'legal_status', 'ai_score', 'risk_level', 'assigned_agent',
                  'installment_available', 'raw_docs', 'ai_analysis',
                  'purpose', 'bedrooms', 'bathrooms', 'floor', 'total_floors', 'year_built',
                  'parking_spaces', 'pets_allowed', 'price_type', 'price_negotiable',
                  'price_on_request', 'deposit', 'service_charge', 'attributes',
                  'is_active', 'primary_image', 'images',
                  'created_at', 'updated_at')
        extra_kwargs = _SIZE_EXTRA_KWARGS
        read_only_fields = ('id', 'ref_no', 'owner', 'owner_phone', 'ai_score', 'risk_level',
                            'size_m2', 'area_sqm',
                            'ai_analysis', 'primary_image', 'images', 'created_at', 'updated_at')

    def get_primary_image(self, obj):
        first = obj.images.first()
        if not first:
            return None
        request = self.context.get('request')
        url = first.image.url
        return request.build_absolute_uri(url) if request else url


class PropertyCreateSerializer(ListingV2ValidationMixin, serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model  = Property
        fields = ('title', 'description', 'city', 'location',
                  'size_value', 'size_unit', 'plot_size_m2',
                  'area_marla', 'area_unit',
                  'price', 'country', 'currency',
                  'property_type', 'furnished_status',
                  'construction_status', 'legal_status', 'assigned_agent', 'owner',
                  'purpose', 'bedrooms', 'bathrooms', 'floor', 'total_floors', 'year_built',
                  'parking_spaces', 'pets_allowed', 'price_type', 'price_negotiable',
                  'price_on_request', 'deposit', 'service_charge', 'attributes')
        extra_kwargs = _SIZE_EXTRA_KWARGS

    def validate_owner(self, value):
        request = self.context.get('request')
        if request and getattr(request.user, 'role', None) != 'admin':
            raise serializers.ValidationError(
                'Ownership is assigned server-side and cannot be set directly.'
            )
        return value

    def validate_price(self, v):
        if v is not None and v <= 0:
            raise serializers.ValidationError('Price must be positive.')
        return v

    def validate_size_value(self, v):
        if v is not None and v <= 0:
            raise serializers.ValidationError('Size must be positive.')
        return v

    def validate_area_marla(self, v):
        if v is not None and v <= 0:
            raise serializers.ValidationError('Area must be positive.')
        return v


class PriceHistorySerializer(serializers.ModelSerializer):
    """Price-history entry (T-1070c). The acting user is not exposed."""

    class Meta:
        model  = PriceHistory
        fields = ('price', 'currency', 'price_type', 'price_on_request', 'changed_at', 'source')
        read_only_fields = fields
