from rest_framework import serializers

from .models import (
    BrokerNetworkPartnership,
    CommissionLedgerEntry,
    SyndicationLeadSubmission,
    SyndicationListing,
)


class SyndicationListingSerializer(serializers.ModelSerializer):
    property_title = serializers.CharField(source='property.title', read_only=True)
    developer_org_name = serializers.CharField(source='developer_org.name', read_only=True)

    class Meta:
        model = SyndicationListing
        fields = (
            'id', 'property', 'property_title', 'developer_org', 'developer_org_name',
            'status', 'commission_type', 'commission_value', 'commission_currency',
            'syndication_scope', 'description', 'expires_at', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'developer_org', 'developer_org_name', 'status', 'created_at', 'updated_at')


class BrokerNetworkPartnershipSerializer(serializers.ModelSerializer):
    developer_org_name = serializers.CharField(source='developer_org.name', read_only=True)
    broker_org_name = serializers.CharField(source='broker_org.name', read_only=True, allow_null=True, default=None)
    broker_agent_name = serializers.CharField(source='broker_agent.name', read_only=True, allow_null=True, default=None)

    class Meta:
        model = BrokerNetworkPartnership
        fields = (
            'id', 'developer_org', 'developer_org_name',
            'broker_org', 'broker_org_name', 'broker_agent', 'broker_agent_name',
            'status', 'commission_override_type', 'commission_override_value',
            'invited_at', 'activated_at', 'notes',
        )
        read_only_fields = ('id', 'developer_org', 'developer_org_name', 'broker_org_name', 'broker_agent_name', 'invited_at', 'activated_at')


class SyndicationLeadSubmissionSerializer(serializers.ModelSerializer):
    listing_title = serializers.CharField(source='listing.property.title', read_only=True)
    lead_name = serializers.CharField(source='lead.name', read_only=True)
    submitted_by_org_name = serializers.CharField(source='submitted_by_org.name', read_only=True, allow_null=True, default=None)

    class Meta:
        model = SyndicationLeadSubmission
        fields = (
            'id', 'listing', 'listing_title', 'lead', 'lead_name',
            'submitted_by_org', 'submitted_by_org_name', 'submitted_by_agent',
            'status', 'commission_calculated', 'commission_currency',
            'deal_lock', 'submitted_at', 'reviewed_at', 'reviewed_by',
        )
        read_only_fields = (
            'id', 'listing_title', 'lead_name', 'submitted_by_org', 'submitted_by_org_name',
            'submitted_by_agent', 'status', 'commission_calculated', 'commission_currency',
            'deal_lock', 'submitted_at', 'reviewed_at', 'reviewed_by',
        )


class CommissionLedgerEntrySerializer(serializers.ModelSerializer):
    developer_org_name = serializers.CharField(source='developer_org.name', read_only=True)
    broker_org_name = serializers.CharField(source='broker_org.name', read_only=True, allow_null=True, default=None)

    class Meta:
        model = CommissionLedgerEntry
        fields = (
            'entry_id', 'listing', 'submission', 'deal_lock',
            'developer_org', 'developer_org_name', 'broker_org', 'broker_org_name', 'broker_agent',
            'commission_amount', 'commission_currency', 'commission_type',
            'source_chain_hash', 'prev_entry_hash', 'status', 'created_at',
        )
        read_only_fields = fields
