from rest_framework import serializers
from .models import Verification, DocumentScan


class DocumentScanSerializer(serializers.ModelSerializer):
    submitter_phone = serializers.CharField(source='phone', read_only=True)
    red_flag_count  = serializers.SerializerMethodField()

    class Meta:
        model  = DocumentScan
        fields = (
            'id', 'submitter_phone', 'document_type', 'owner_name',
            'cnic_number', 'property_address', 'area', 'authority',
            'confidence', 'status', 'red_flag_count', 'red_flags',
            'whatsapp_summary', 'verification', 'created_at',
        )
        read_only_fields = ('id', 'created_at')

    def get_red_flag_count(self, obj):
        return len(obj.red_flags) if obj.red_flags else 0


class VerificationSerializer(serializers.ModelSerializer):
    property_title    = serializers.CharField(source='property.title', read_only=True)
    property_city     = serializers.CharField(source='property.city',  read_only=True)
    property_id       = serializers.UUIDField(source='property.id',    read_only=True)
    requester_phone   = serializers.CharField(source='requested_by.phone', read_only=True, allow_null=True)
    reviewer_phone    = serializers.CharField(source='reviewer.phone',     read_only=True, allow_null=True)
    document_count    = serializers.SerializerMethodField()
    total_red_flags   = serializers.SerializerMethodField()
    document_types    = serializers.SerializerMethodField()

    class Meta:
        model  = Verification
        fields = (
            'id', 'status', 'signal_score',
            'property_id', 'property_title', 'property_city',
            'requester_phone', 'reviewer_phone',
            'document_count', 'total_red_flags', 'document_types',
            'fraud_flags', 'notes', 'verified_at', 'created_at',
        )
        read_only_fields = (
            'id', 'property_id', 'property_title', 'property_city',
            'requester_phone', 'reviewer_phone', 'signal_score',
            'document_count', 'total_red_flags', 'document_types',
            'fraud_flags', 'created_at',
        )

    def get_document_count(self, obj):
        return obj.document_scans.count()

    def get_total_red_flags(self, obj):
        return sum(len(s.red_flags) for s in obj.document_scans.all())

    def get_document_types(self, obj):
        return list(obj.document_scans.values_list('document_type', flat=True).distinct())
