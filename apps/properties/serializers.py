from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from .models import Amenity, PriceHistory, Property, PropertyImage
from . import taxonomy
from apps.users.models import User


class PropertyImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model  = PropertyImage
        fields = ('id', 'url', 'thumbnail_url', 'caption', 'alt_text', 'order', 'width', 'height',
                  'created_at')
        read_only_fields = ('id', 'url', 'thumbnail_url', 'order', 'width', 'height', 'created_at')

    def _abs(self, f) -> str | None:
        if not f:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(f.url) if request else f.url

    def get_url(self, obj) -> str | None:
        return self._abs(obj.image)

    def get_thumbnail_url(self, obj) -> str | None:
        return self._abs(obj.thumbnail) or self._abs(obj.image)


V2_FIELDS = ('purpose', 'bedrooms', 'bathrooms', 'floor', 'total_floors', 'year_built',
             'parking_spaces', 'pets_allowed', 'price_type', 'price_negotiable',
             'price_on_request', 'deposit', 'service_charge', 'attributes')


TAXONOMY_FIELDS = ('category', 'subtype')

_DEPRECATED = 'Deprecated alias of {0} (T-1070b/g); no column of its own. Send {0} instead.'
_SIZE_EXTRA_KWARGS = {
    'category':   {'help_text': 'Listing category (default residential; property_type is a '
                                'deprecated alias).'},
    'subtype':    {'help_text': 'Must belong to the category (see GET properties/taxonomy/).'},
    'size_value': {'help_text': 'Size in size_unit.'},
    'size_m2':    {'help_text': 'Canonical size in m², computed from size_value + size_unit.'},
}


class _LegacyAlias:
    """
    Deprecated v1 name kept in the API after its column left the model
    (T-1070g-1). Reads ``getter(instance)``; a written value lands in
    ``validated_data`` under the alias name and ListingV2ValidationMixin maps
    it onto the v2 field.
    """

    def __init__(self, *args, getter, **kwargs):
        self._getter = getter
        kwargs.setdefault('required', False)
        super().__init__(*args, **kwargs)

    def get_attribute(self, instance):
        return self._getter(instance)


class LegacyDecimalAlias(_LegacyAlias, serializers.DecimalField):
    pass


class LegacyChoiceAlias(_LegacyAlias, serializers.ChoiceField):
    pass


_PT_HELP = ('Deprecated alias of category (T-1070d/g): land → plot, hospitality → commercial. '
            'Send category instead.')


def _area_marla(**kw):
    return LegacyDecimalAlias(max_digits=10, decimal_places=2, getter=lambda o: o.size_value,
                              help_text=_DEPRECATED.format('size_value'), **kw)


def _area_unit(**kw):
    return LegacyChoiceAlias(choices=Property.AreaUnit.choices, getter=lambda o: o.size_unit,
                             help_text=_DEPRECATED.format('size_unit'), **kw)


def _property_type(**kw):
    return LegacyChoiceAlias(choices=Property.PropertyType.choices,
                             getter=lambda o: o.property_type, help_text=_PT_HELP, **kw)


def _area_sqm():
    return LegacyDecimalAlias(max_digits=12, decimal_places=4, getter=lambda o: o.size_m2,
                              read_only=True, help_text=_DEPRECATED.format('size_m2'))


class LegacyReadAliases(serializers.Serializer):
    """Read-only v1 names (list views)."""
    area_marla    = _area_marla(read_only=True)
    area_unit     = _area_unit(read_only=True)
    area_sqm      = _area_sqm()
    property_type = _property_type(read_only=True)


class LegacyWriteAliases(serializers.Serializer):
    """Writable v1 names; ListingV2ValidationMixin maps them onto v2 fields."""
    area_marla    = _area_marla(allow_null=True)
    area_unit     = _area_unit()
    property_type = _property_type()


class AmenityKeysField(serializers.SlugRelatedField):
    """Amenities read and written as a list of taxonomy keys (T-1070d)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('slug_field', 'key')
        if not kwargs.get('read_only'):
            kwargs.setdefault('queryset', Amenity.objects.filter(is_active=True))
        kwargs.setdefault('help_text', 'Amenity keys (see GET properties/taxonomy/).')
        super().__init__(**kwargs)


class LocationRefField(serializers.PrimaryKeyRelatedField):
    """Write a location id; omit it to let the server match city/location text (T-1060a)."""

    def __init__(self, **kwargs):
        from apps.locations.models import Location
        kwargs.setdefault('queryset', Location.objects.filter(is_active=True))
        kwargs.setdefault('source', 'location_ref')
        kwargs.setdefault('required', False)
        kwargs.setdefault('allow_null', True)
        kwargs.setdefault('help_text', 'Location id (GET /locations/). Omit to match from city/location.')
        super().__init__(**kwargs)


@extend_schema_field({'type': 'object', 'nullable': True, 'properties': {
    'id': {'type': 'integer'}, 'name': {'type': 'string'}, 'level': {'type': 'string'},
    'path': {'type': 'string'}}})
class LocationRefOut(serializers.Field):
    """Matched location node (read-only), ``null`` when not linked yet."""

    def __init__(self, **kwargs):
        kwargs['read_only'] = True
        super().__init__(**kwargs)

    def to_representation(self, loc):
        return {'id': loc.pk, 'name': loc.name, 'level': loc.level, 'path': loc.path}


def viewer_sees_exact_point(context, prop) -> bool:
    """Owner, the listing's organisation, or staff see exact coordinates (FR-LST-003)."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return False
    if user.is_staff or getattr(user, 'role', None) == 'admin' or prop.owner_id == user.pk:
        return True
    if not prop.organization_id:
        return False
    if '_viewer_org_id' not in context:                     # once per response
        from apps.core.permissions import get_user_org
        org = get_user_org(user)
        context['_viewer_org_id'] = org.pk if org else None
    return context['_viewer_org_id'] == prop.organization_id


class GeoPointMixin:
    """
    latitude/longitude: exact for privileged viewers, else the public point
    (snapped to ~1 km for private-seller/approximate listings). distance_km
    (from ?near=) is rounded to 0.1 km, or to whole km when the point is snapped.
    """

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if 'latitude' in data and not viewer_sees_exact_point(self.context, instance):
            data['latitude'] = _coord(instance.geo_lat)
            data['longitude'] = _coord(instance.geo_lng)
        if 'distance_km' in data:
            d = getattr(instance, 'distance_km', None)
            exact = instance.geo_lat is not None and instance.geo_lat == instance.latitude \
                and instance.geo_lng == instance.longitude
            data['distance_km'] = None if d is None else (round(d, 1) if exact else float(round(d)))
        return data

    def validate(self, attrs):
        attrs = super().validate(attrs)
        lat = attrs.get('latitude', getattr(self.instance, 'latitude', None))
        lng = attrs.get('longitude', getattr(self.instance, 'longitude', None))
        if (lat is None) != (lng is None):
            raise serializers.ValidationError({'latitude': 'Send latitude and longitude together.'})
        if lat is not None:
            from apps.geo.search import validate_point
            try:
                validate_point(lat, lng)
            except ValueError as exc:
                raise serializers.ValidationError({'latitude': str(exc)})
        return attrs


def _coord(v):
    return None if v is None else f'{v:.6f}'


class ListingV2ValidationMixin:
    """Cross-field rules for listing schema v2 (T-1070a/b/d); partial updates merge the instance."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        for new, old in (('size_value', 'area_marla'), ('size_unit', 'area_unit')):
            if old not in attrs:
                continue
            value = attrs.pop(old)
            if new in attrs and attrs[new] != value:
                raise serializers.ValidationError(
                    {old: f'Conflicts with {new}; send only {new} ({old} is deprecated).'})
            attrs[new] = value
        fields = V2_FIELDS + TAXONOMY_FIELDS
        if self.instance:
            merged = {f: getattr(self.instance, f, None) for f in fields}
        else:
            merged = {'category': Property.Category.RESIDENTIAL}
        merged.update({k: v for k, v in attrs.items() if k in fields})
        self._merge_taxonomy(attrs, merged)
        errors = Property.v2_errors(merged)
        if errors:
            raise serializers.ValidationError(errors)
        if 'attributes' in attrs or 'country' in attrs:
            self._validate_attributes(attrs)
        if 'amenities' in attrs:
            self._validate_amenities(attrs['amenities'], attrs)
        if attrs.get('location_ref') is not None:
            market = self._target_market(attrs)
            if attrs['location_ref'].market_id != market:
                raise serializers.ValidationError(
                    {'location_id': f'Location is not in market {market}.'})
        return attrs

    def _target_market(self, attrs):
        """Market the written listing will be validated against."""
        from apps.markets.services import default_country, market_code_for_country
        if self.instance is not None and 'country' not in attrs:
            return self.instance.market_id
        country = attrs.get('country') or getattr(self.instance, 'country', None) or default_country()
        return market_code_for_country(country)

    def _validate_attributes(self, attrs):
        """Market attribute schema (T-1070e). Stored attributes are re-checked only on a market move."""
        attributes = attrs.get('attributes', getattr(self.instance, 'attributes', None)) or {}
        errors = Property.attribute_errors(attributes, self._target_market(attrs))
        if errors:
            raise serializers.ValidationError({'attributes': errors})

    @staticmethod
    def _merge_taxonomy(attrs, merged):
        """Map the deprecated property_type alias onto category."""
        if 'property_type' in attrs:
            ptype = attrs.pop('property_type')
            if 'category' in attrs:
                if attrs['category'] and taxonomy.property_type_for_category(attrs['category']) != ptype:
                    raise serializers.ValidationError({'property_type': (
                        'Conflicts with category; send only category (property_type is deprecated).')})
            elif taxonomy.property_type_for_category(merged.get('category')) != ptype:
                # 'commercial' keeps a hospitality listing hospitality (same v1 value).
                attrs['category'] = merged['category'] = taxonomy.category_for_property_type(ptype)
        if not merged.get('category'):
            merged['category'] = Property.Category.RESIDENTIAL
        # A stored subtype that no longer fits a changed category is cleared;
        # an explicitly sent one is validated (→ 400).
        if 'subtype' not in attrs and merged.get('subtype') and \
                taxonomy.subtype_errors(merged['category'], merged['subtype']):
            attrs['subtype'] = merged['subtype'] = None

    def _validate_amenities(self, amenities, attrs):
        if len(amenities) > Property.MAX_AMENITIES:
            raise serializers.ValidationError(
                {'amenities': f'At most {Property.MAX_AMENITIES} amenities.'})
        market = self._target_market(attrs)
        foreign = sorted(a.key for a in amenities if not a.available_in(market))
        if foreign:
            raise serializers.ValidationError(
                {'amenities': f"Not available in market {market}: {', '.join(foreign)}."})

    def create(self, validated_data):
        with transaction.atomic():
            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            return super().update(instance, validated_data)


class PropertyListSerializer(GeoPointMixin, LegacyReadAliases, serializers.ModelSerializer):
    """Lightweight serializer for list views."""
    primary_image = serializers.SerializerMethodField()
    amenities     = AmenityKeysField(many=True, read_only=True)
    location_ref  = LocationRefOut()
    distance_km   = serializers.FloatField(read_only=True, required=False, default=None,
                                           help_text='Only with ?near=: km to the public point.')

    class Meta:
        model  = Property
        fields = ('id', 'ref_no', 'title', 'city', 'location', 'latitude', 'longitude',
                  'location_precision', 'distance_km',
                  'size_value', 'size_unit', 'size_m2',
                  'area_marla', 'area_unit', 'area_sqm',
                  'price', 'country', 'currency',
                  'purpose', 'price_type', 'price_on_request', 'bedrooms', 'bathrooms',
                  'category', 'subtype', 'amenities', 'location_ref',
                  'property_type', 'construction_status', 'furnished_status',
                  'legal_status', 'ai_score', 'risk_level', 'primary_image', 'status',
                  'created_at')
        extra_kwargs = _SIZE_EXTRA_KWARGS

    def get_primary_image(self, obj) -> str | None:
        first = obj.images.first()
        if not first:
            return None
        request = self.context.get('request')
        url = first.image.url
        return request.build_absolute_uri(url) if request else url


class PropertyDetailSerializer(GeoPointMixin, ListingV2ValidationMixin, LegacyWriteAliases,
                               serializers.ModelSerializer):
    owner_phone   = serializers.CharField(source='owner.phone', read_only=True)
    images        = PropertyImageSerializer(many=True, read_only=True)
    primary_image = serializers.SerializerMethodField()
    amenities     = AmenityKeysField(many=True, required=False)
    location_id   = LocationRefField()
    location_ref  = LocationRefOut()
    area_sqm      = _area_sqm()

    class Meta:
        model  = Property
        fields = ('id', 'ref_no', 'owner', 'owner_phone', 'title', 'description',
                  'city', 'location', 'size_value', 'size_unit', 'size_m2', 'plot_size_m2',
                  'area_marla', 'area_unit', 'area_sqm',
                  'price', 'country', 'currency',
                  'property_type', 'category', 'subtype', 'amenities',
                  'location_id', 'location_ref', 'location_precision', 'latitude', 'longitude',
                  'furnished_status', 'construction_status',
                  'legal_status', 'ai_score', 'risk_level', 'assigned_agent',
                  'installment_available', 'raw_docs', 'ai_analysis',
                  'purpose', 'bedrooms', 'bathrooms', 'floor', 'total_floors', 'year_built',
                  'parking_spaces', 'pets_allowed', 'price_type', 'price_negotiable',
                  'price_on_request', 'deposit', 'service_charge', 'attributes',
                  'is_active', 'status', 'status_changed_at', 'published_at', 'expires_at',
                  'primary_image', 'images',
                  'created_at', 'updated_at')
        extra_kwargs = _SIZE_EXTRA_KWARGS
        # status changes go through POST …/transitions/ (T-1080); a PATCH of
        # is_active is mapped to published / archived there too.
        read_only_fields = ('id', 'ref_no', 'owner', 'owner_phone', 'ai_score', 'risk_level',
                            'size_m2', 'status', 'status_changed_at', 'published_at', 'expires_at',
                            'ai_analysis', 'primary_image', 'images', 'created_at', 'updated_at')

    # Only for people who manage the listing: consumers contact the lister through an enquiry.
    _PRIVATE = ('owner_phone', 'raw_docs', 'ai_analysis')

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if not self._manages(obj):
            data.update(dict.fromkeys(self._PRIVATE))
        return data

    def _manages(self, obj) -> bool:
        from .lifecycle import can_manage
        user = getattr(self.context.get('request'), 'user', None)
        return viewer_sees_exact_point(self.context, obj) or can_manage(user, obj)

    def get_primary_image(self, obj) -> str | None:
        first = obj.images.first()
        if not first:
            return None
        request = self.context.get('request')
        url = first.image.url
        return request.build_absolute_uri(url) if request else url


class PropertyCreateSerializer(GeoPointMixin, ListingV2ValidationMixin, LegacyWriteAliases,
                               serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True
    )
    amenities = AmenityKeysField(many=True, required=False)
    location_id = LocationRefField()
    status = serializers.ChoiceField(
        choices=['draft', 'pending_review', 'published'], required=False,
        help_text='Initial status (default published; a market that reviews listings turns '
                  'published into pending_review).')

    class Meta:
        model  = Property
        fields = ('title', 'description', 'city', 'location', 'status',
                  'size_value', 'size_unit', 'plot_size_m2',
                  'area_marla', 'area_unit',
                  'price', 'country', 'currency',
                  'property_type', 'category', 'subtype', 'amenities', 'furnished_status',
                  'location_id', 'location_precision', 'latitude', 'longitude',
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


class AmenitySerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()

    class Meta:
        model  = Amenity
        fields = ('key', 'group', 'label', 'labels', 'markets')
        read_only_fields = fields

    def get_label(self, obj) -> str:
        return obj.label(self.context.get('lang') or 'en')



class ListingTransitionSerializer(serializers.ModelSerializer):
    """Audit row (T-1080). The actor is shown only as a kind, not an identity."""
    actor_kind = serializers.SerializerMethodField()

    class Meta:
        from .models import ListingTransition
        model  = ListingTransition
        fields = ('from_status', 'to_status', 'source', 'reason', 'actor_kind', 'created_at')
        read_only_fields = fields

    def get_actor_kind(self, obj) -> str:
        if obj.actor_id is None:
            return 'system'
        return 'staff' if getattr(obj.actor, 'role', None) == 'admin' else 'lister'


class TransitionRequestSerializer(serializers.Serializer):
    to     = serializers.ChoiceField(choices=Property.Status.choices)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
