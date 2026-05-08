from rest_framework import serializers
from .models import User


class SendOTPSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value):
        # Pakistan phone format: +923XXXXXXXXX  (12 digits + plus sign)
        v = value.strip().replace(' ', '').replace('-', '')
        if not v.startswith('+'):
            v = '+' + v
        if not v.startswith('+92') or len(v) != 13:
            raise serializers.ValidationError(
                'Phone must be a valid Pakistani number in +92XXXXXXXXXX format.'
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
                  'ntn', 'cnic', 'last_active', 'created_at')
        read_only_fields = ('id', 'phone', 'role', 'last_active', 'created_at')


class UserListSerializer(serializers.ModelSerializer):
    date_joined = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model  = User
        fields = ('id', 'phone', 'name', 'email', 'role', 'is_active', 'date_joined')
        read_only_fields = ('id', 'phone', 'date_joined')