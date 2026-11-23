from rest_framework import serializers

from .models import Location


class LocationSerializer(serializers.ModelSerializer):
    parent_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model  = Location
        fields = ('id', 'name', 'level', 'path', 'parent_id', 'market', 'aliases', 'is_verified')
        read_only_fields = fields


class LocationRefSerializer(serializers.ModelSerializer):
    """Compact form embedded in listings."""
    class Meta:
        model  = Location
        fields = ('id', 'name', 'level', 'path')
        read_only_fields = fields
