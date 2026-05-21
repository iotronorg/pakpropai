from rest_framework import serializers
from .models import Organization


class OrganizationListSerializer(serializers.ModelSerializer):
    """Minimal fields for list responses."""
    admin_phone = serializers.CharField(source='admin_user.phone', read_only=True, allow_null=True)

    class Meta:
        model  = Organization
        fields = (
            'id', 'name', 'slug', 'org_type', 'plan',
            'country', 'city', 'phone', 'email', 'website',
            'is_active', 'is_verified',
            'admin_phone',
            'created_at',
        )


class OrganizationDetailSerializer(serializers.ModelSerializer):
    """Full fields for detail / create / update."""
    admin_phone = serializers.CharField(source='admin_user.phone', read_only=True, allow_null=True)
    admin_name  = serializers.CharField(source='admin_user.name',  read_only=True, allow_null=True)

    class Meta:
        model  = Organization
        fields = (
            'id', 'name', 'slug', 'org_type', 'plan',
            'admin_user', 'admin_phone', 'admin_name',
            'phone', 'email', 'website', 'logo',
            'country', 'language', 'city', 'address',
            'is_active', 'is_verified',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'slug', 'admin_phone', 'admin_name',
            'created_at', 'updated_at',
        )

    def validate_admin_user(self, user):
        from apps.users.models import User
        if user and user.role not in (User.Role.DEVELOPER, User.Role.ADMIN):
            raise serializers.ValidationError(
                "admin_user must have role 'developer' or 'admin'."
            )
        return user
