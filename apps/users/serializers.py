import re
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from .models import User

_E164_RE = re.compile(r'^\+\d{7,15}$')


class SendOTPSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value):
        v = value.strip().replace(' ', '').replace('-', '')
        if not v.startswith('+'):
            v = '+' + v
        if not _E164_RE.match(v):
            raise serializers.ValidationError(
                'Phone must be in E.164 format, e.g. +923001234567.'
            )
        return v


class VerifyOTPSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    code  = serializers.CharField(max_length=6, min_length=6)

    def validate_phone(self, value):
        v = value.strip().replace(' ', '').replace('-', '')
        if not v.startswith('+'):
            v = '+' + v
        return v


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model  = User
        fields = ('id', 'phone', 'name', 'email', 'role', 'is_filer',
                  'ntn', 'cnic', 'last_active', 'created_at', 'is_phone_verified')
        read_only_fields = ('id', 'phone', 'role', 'last_active', 'created_at')
        extra_kwargs = {'is_phone_verified': {'read_only': True}}


class UserListSerializer(serializers.ModelSerializer):
    date_joined = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model  = User
        fields = ('id', 'phone', 'name', 'email', 'role', 'is_active',
                  'date_joined', 'last_active', 'ntn', 'cnic', 'is_filer')
        read_only_fields = ('id', 'phone', 'date_joined', 'last_active')


class UserCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = User
        fields = ('phone', 'name', 'email', 'role')

    def validate_phone(self, value):
        v = value.strip().replace(' ', '').replace('-', '')
        if not v.startswith('+'):
            v = '+' + v
        if not _E164_RE.match(v):
            raise serializers.ValidationError(
                'Phone must be in E.164 format, e.g. +923001234567.'
            )
        return v

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class PasswordLoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=254)   # phone (E.164) or email
    password   = serializers.CharField(write_only=True)


class PasswordResetRequestSerializer(serializers.Serializer):
    phone = serializers.RegexField(r'^\+\d{7,15}$')


class PasswordResetConfirmSerializer(serializers.Serializer):
    phone        = serializers.RegexField(r'^\+\d{7,15}$')
    code         = serializers.CharField(min_length=6, max_length=6)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password     = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


class RegistrationOTPVerifySerializer(serializers.Serializer):
    phone = serializers.RegexField(r'^\+\d{7,15}$')
    code  = serializers.CharField(min_length=6, max_length=6)