import re

from rest_framework import serializers

from .models import Organization, OrganizationTheme


class OrganizationListSerializer(serializers.ModelSerializer):
    """Minimal fields for list responses."""
    admin_phone    = serializers.CharField(source='admin_user.phone', read_only=True, allow_null=True)
    agent_count    = serializers.SerializerMethodField()
    lead_count     = serializers.SerializerMethodField()
    property_count = serializers.SerializerMethodField()

    class Meta:
        model  = Organization
        fields = (
            'id', 'name', 'slug', 'org_type', 'plan',
            'country', 'city', 'phone', 'email', 'website',
            'is_active', 'is_verified',
            'admin_phone',
            'agent_count', 'lead_count', 'property_count',
            'created_at',
        )

    def get_agent_count(self, obj):
        from apps.agents.models import Agent
        return Agent.objects.filter(organization=obj, is_active=True).count()

    def get_lead_count(self, obj):
        from apps.leads.models import Lead
        return Lead.objects.filter(organization=obj).count()

    def get_property_count(self, obj):
        from apps.properties.models import Property
        return Property.objects.filter(organization=obj, is_active=True).count()


class OrganizationDetailSerializer(serializers.ModelSerializer):
    """Full fields for detail / create / update."""
    admin_phone    = serializers.CharField(source='admin_user.phone', read_only=True, allow_null=True)
    admin_name     = serializers.CharField(source='admin_user.name',  read_only=True, allow_null=True)
    agent_count    = serializers.SerializerMethodField()
    lead_count     = serializers.SerializerMethodField()
    property_count = serializers.SerializerMethodField()

    class Meta:
        model  = Organization
        fields = (
            'id', 'name', 'slug', 'org_type', 'plan',
            'admin_user', 'admin_phone', 'admin_name',
            'phone', 'email', 'website', 'logo', 'brand_color',
            'country', 'language', 'measurement_system', 'city', 'address',
            'is_active', 'is_verified',
            'agent_count', 'lead_count', 'property_count',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'slug', 'admin_phone', 'admin_name',
            'agent_count', 'lead_count', 'property_count',
            'created_at', 'updated_at',
        )

    def get_agent_count(self, obj):
        from apps.agents.models import Agent
        return Agent.objects.filter(organization=obj, is_active=True).count()

    def get_lead_count(self, obj):
        from apps.leads.models import Lead
        return Lead.objects.filter(organization=obj).count()

    def get_property_count(self, obj):
        from apps.properties.models import Property
        return Property.objects.filter(organization=obj, is_active=True).count()

    def validate_admin_user(self, user):
        from apps.users.models import User
        if user and user.role not in (User.Role.DEVELOPER, User.Role.ADMIN):
            raise serializers.ValidationError(
                "admin_user must have role 'developer' or 'admin'."
            )
        return user


_E164_RE = re.compile(r'^\+\d{7,15}$')


class OrgRegistrationSerializer(serializers.Serializer):
    """Public self-service org signup — no authentication required."""
    org_name   = serializers.CharField(max_length=200)
    org_type   = serializers.ChoiceField(choices=Organization.OrgType.choices)
    country    = serializers.CharField(max_length=2, min_length=2)
    admin_name = serializers.CharField(max_length=200)
    phone      = serializers.CharField(max_length=20)
    email      = serializers.EmailField()
    password   = serializers.CharField(min_length=8, write_only=True)

    def validate_phone(self, value):
        if not _E164_RE.match(value):
            raise serializers.ValidationError(
                'Phone must be in E.164 format, e.g. +441234567890.'
            )
        from apps.users.models import User
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError('An account with this phone already exists.')
        return value

    def validate_email(self, value):
        from apps.users.models import User
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value

    def validate_country(self, value):
        return value.upper()


class OrgRegistrationOTPVerifySerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    code  = serializers.CharField(max_length=10)


class OrganizationThemeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = OrganizationTheme
        fields = ('primary_color', 'secondary_color', 'accent_color', 'logo_url', 'updated_at')
        read_only_fields = ('updated_at',)
