import re
from django.db import transaction
from rest_framework import serializers
from .models import Agent
from apps.core.validators import validate_photo
from apps.organizations.models import Organization


class AgentSerializer(serializers.ModelSerializer):
    user_phone = serializers.CharField(source='user.phone', read_only=True, allow_null=True)
    user_email = serializers.CharField(source='user.email', read_only=True, allow_null=True)
    organization_name = serializers.CharField(
        source='organization.name', read_only=True, allow_null=True, default=None
    )
    class Meta:
        model = Agent
        fields = (
            'id', 'name', 'agent_type', 'employment_type', 'phone', 'whatsapp_number', 'email',
            'company_name', 'designation', 'bio',
            'specializations', 'cities', 'areas', 'primary_city',
            'is_verified', 'is_active', 'is_featured',
            'availability_status',
            'registration_status', 'rejection_reason',
            'total_leads', 'total_listings', 'closed_deals', 'rating',
            'user_phone', 'user_email',
            'organization', 'organization_name',
            'profile_photo',
            'joined_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'is_verified', 'total_leads', 'total_listings',
            'closed_deals', 'rating', 'user_phone', 'user_email',
            'organization_name',
            'joined_at', 'updated_at',
            'registration_status', 'rejection_reason',
        )

    def validate_profile_photo(self, value):
        if value:
            validate_photo(value)
        return value


class AgentRegistrationSerializer(serializers.Serializer):
    """Public endpoint — creates a User + Agent in a single transaction."""

    # Auth account
    phone = serializers.CharField(max_length=20)
    name  = serializers.CharField(max_length=200)

    # Profile
    agent_type       = serializers.ChoiceField(choices=Agent.AgentType.choices, default=Agent.AgentType.INDIVIDUAL)
    email            = serializers.EmailField(required=False, allow_blank=True, default='')
    whatsapp_number  = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    company_name     = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    designation      = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    license_number   = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    years_experience = serializers.IntegerField(min_value=0, required=False, default=0)
    bio              = serializers.CharField(required=False, allow_blank=True, default='')
    primary_city     = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    cities           = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    areas            = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    specializations  = serializers.ListField(child=serializers.CharField(), required=False, default=list)

    # Optional: link to an Organization at registration time
    organization = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    def validate_phone(self, value):
        from apps.users.models import User
        if not re.match(r'^\+\d{7,15}$', value):
            raise serializers.ValidationError("Phone must be in E.164 format (e.g. +12025550123).")
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        if Agent.objects.filter(phone=value).exists():
            raise serializers.ValidationError("An agent profile with this phone number already exists.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        from apps.users.models import User

        phone = validated_data['phone']
        name  = validated_data['name']

        user = User.objects.create_user(
            phone=phone,
            name=name,
            role=User.Role.AGENT,
            is_active=False,   # locked until approved
        )

        org = validated_data.get('organization')
        agent = Agent.objects.create(
            user=user,
            name=name,
            phone=phone,
            agent_type=validated_data['agent_type'],
            employment_type=(
                Agent.EmploymentType.INTERNAL if org else Agent.EmploymentType.FREELANCE
            ),
            email=validated_data.get('email', ''),
            whatsapp_number=validated_data.get('whatsapp_number', ''),
            company_name=validated_data.get('company_name', ''),
            designation=validated_data.get('designation', ''),
            license_number=validated_data.get('license_number', ''),
            years_experience=validated_data.get('years_experience', 0),
            bio=validated_data.get('bio', ''),
            primary_city=validated_data.get('primary_city', ''),
            cities=validated_data.get('cities', []),
            areas=validated_data.get('areas', []),
            specializations=validated_data.get('specializations', []),
            organization=org,
            registration_status=Agent.RegistrationStatus.PENDING,
            is_verified=False,
            is_active=False,
        )
        return agent
