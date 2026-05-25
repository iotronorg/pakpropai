from rest_framework import serializers
from .models import Organization


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
