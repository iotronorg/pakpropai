import logging

from django.core.exceptions import PermissionDenied
from django.utils import timezone
from rest_framework import mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from apps.core.permissions import IsAdminUser, get_user_org

from .broker_network import BrokerNetworkService
from .commission_ledger import CommissionLedger
from .models import (
    BrokerNetworkPartnership,
    CommissionLedgerEntry,
    SyndicationLeadSubmission,
    SyndicationListing,
)
from .serializers import (
    BrokerNetworkPartnershipSerializer,
    CommissionLedgerEntrySerializer,
    SyndicationLeadSubmissionSerializer,
    SyndicationListingSerializer,
)
from .syndication_service import SyndicationVisibilityLayer

logger = logging.getLogger(__name__)


def _is_developer(user):
    return user.is_authenticated and user.role == 'developer'


def _is_agent(user):
    return user.is_authenticated and user.role == 'agent'


def _is_admin(user):
    return user.is_authenticated and user.role == 'admin'


class SyndicationListingViewSet(ModelViewSet):
    """Developer manages own syndication listings. Admin sees all."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SyndicationListingSerializer

    def get_queryset(self):
        user = self.request.user
        if _is_admin(user):
            return SyndicationListing.objects.select_related('developer_org', 'property').all()
        if _is_developer(user):
            org = get_user_org(user)
            if org:
                return SyndicationListing.objects.filter(developer_org=org).select_related('developer_org', 'property')
        return SyndicationListing.objects.none()

    def get_permissions(self):
        if self.action in ('syndicate', 'withdraw', 'create', 'update', 'partial_update', 'destroy'):
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    def perform_create(self, serializer):
        user = self.request.user
        if not _is_developer(user) and not _is_admin(user):
            raise PermissionDenied('Only developer orgs can create syndication listings.')
        org = get_user_org(user)
        if not org:
            raise PermissionDenied('No organization found for user.')
        serializer.save(developer_org=org)

    @action(detail=True, methods=['post'])
    def syndicate(self, request, pk=None):
        listing = self.get_object()
        org = get_user_org(request.user)
        if not org:
            return Response({'detail': 'No organization.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            updated = SyndicationVisibilityLayer().syndicate_listing(listing, org)
        except PermissionDenied as e:
            return Response({'detail': str(e)}, status=status.HTTP_403_FORBIDDEN)
        return Response(SyndicationListingSerializer(updated).data)

    @action(detail=True, methods=['post'])
    def withdraw(self, request, pk=None):
        listing = self.get_object()
        org = get_user_org(request.user)
        if not org:
            return Response({'detail': 'No organization.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            updated = SyndicationVisibilityLayer().withdraw_listing(listing, org)
        except PermissionDenied as e:
            return Response({'detail': str(e)}, status=status.HTTP_403_FORBIDDEN)
        return Response(SyndicationListingSerializer(updated).data)


class BrokerNetworkPartnershipViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    GenericViewSet,
):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BrokerNetworkPartnershipSerializer

    def get_queryset(self):
        user = self.request.user
        if _is_admin(user):
            return BrokerNetworkPartnership.objects.select_related('developer_org', 'broker_org', 'broker_agent').all()
        if _is_developer(user):
            org = get_user_org(user)
            if org:
                return BrokerNetworkPartnership.objects.filter(developer_org=org).select_related('developer_org', 'broker_org', 'broker_agent')
        if _is_agent(user):
            return BrokerNetworkPartnership.objects.filter(broker_agent=user).select_related('developer_org', 'broker_org', 'broker_agent')
        return BrokerNetworkPartnership.objects.none()

    @action(detail=False, methods=['post'], url_path='invite')
    def invite(self, request):
        if not _is_developer(request.user) and not _is_admin(request.user):
            return Response({'detail': 'Only developer orgs can invite partners.'}, status=status.HTTP_403_FORBIDDEN)
        org = get_user_org(request.user)
        if not org:
            return Response({'detail': 'No organization.'}, status=status.HTTP_403_FORBIDDEN)

        broker_org_id = request.data.get('broker_org')
        broker_agent_id = request.data.get('broker_agent')
        from apps.organizations.models import Organization
        from apps.users.models import User
        broker_org = Organization.objects.filter(pk=broker_org_id).first() if broker_org_id else None
        broker_agent = User.objects.filter(pk=broker_agent_id).first() if broker_agent_id else None

        try:
            partnership = BrokerNetworkService().invite_partner(
                developer_org=org,
                broker_org=broker_org,
                broker_agent=broker_agent,
                commission_override_type=request.data.get('commission_override_type'),
                commission_override_value=request.data.get('commission_override_value'),
                notes=request.data.get('notes', ''),
            )
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(BrokerNetworkPartnershipSerializer(partnership).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        partnership = self.get_object()
        try:
            updated = BrokerNetworkService().accept_invitation(partnership, request.user)
        except PermissionDenied as e:
            return Response({'detail': str(e)}, status=status.HTTP_403_FORBIDDEN)
        return Response(BrokerNetworkPartnershipSerializer(updated).data)

    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        org = get_user_org(request.user)
        if not org:
            return Response({'detail': 'No organization.'}, status=status.HTTP_403_FORBIDDEN)
        partnership = self.get_object()
        try:
            updated = BrokerNetworkService().revoke_partnership(partnership, org)
        except PermissionDenied as e:
            return Response({'detail': str(e)}, status=status.HTTP_403_FORBIDDEN)
        return Response(BrokerNetworkPartnershipSerializer(updated).data)


class CommissionLedgerView(
    mixins.ListModelMixin,
    GenericViewSet,
):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CommissionLedgerEntrySerializer

    def get_queryset(self):
        user = self.request.user
        if _is_admin(user):
            return CommissionLedgerEntry.objects.select_related('listing', 'developer_org', 'broker_org').all()
        if _is_developer(user):
            org = get_user_org(user)
            if org:
                return CommissionLedgerEntry.objects.filter(developer_org=org)
        if _is_agent(user):
            return CommissionLedgerEntry.objects.filter(broker_agent=user)
        return CommissionLedgerEntry.objects.none()

    @action(detail=False, methods=['get'], url_path='verify-chain')
    def verify_chain(self, request):
        if not _is_admin(request.user):
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        org_id = request.query_params.get('org')
        if not org_id:
            return Response({'detail': 'Pass ?org=<uuid> to verify a specific org chain.'}, status=status.HTTP_400_BAD_REQUEST)
        from apps.organizations.models import Organization
        org = Organization.objects.filter(pk=org_id).first()
        if not org:
            return Response({'detail': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)
        valid, errors = CommissionLedger().verify_chain_integrity(org)
        return Response({'valid': valid, 'errors': errors})


# ── Broker / Agent views ──────────────────────────────────────────────────────

class SyndicationBrowseView(APIView):
    """GET /marketplace/browse/ — visible syndicated listings for broker/agent."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        if _is_developer(user) or _is_admin(user):
            return Response({'detail': 'Developers do not browse syndicated inventory.'}, status=status.HTTP_403_FORBIDDEN)
        org = get_user_org(user)
        agent = user if _is_agent(user) else None
        listings = SyndicationVisibilityLayer().get_visible_listings(org, agent)
        return Response(SyndicationListingSerializer(listings, many=True).data)


class SyndicationLeadSubmissionViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    GenericViewSet,
):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SyndicationLeadSubmissionSerializer

    def get_queryset(self):
        user = self.request.user
        if _is_admin(user):
            return SyndicationLeadSubmission.objects.select_related('listing', 'lead', 'submitted_by_org').all()
        if _is_developer(user):
            org = get_user_org(user)
            if org:
                return SyndicationLeadSubmission.objects.filter(listing__developer_org=org).select_related('listing', 'lead')
        if _is_agent(user):
            return SyndicationLeadSubmission.objects.filter(submitted_by_agent=user).select_related('listing', 'lead')
        org = get_user_org(user)
        if org:
            return SyndicationLeadSubmission.objects.filter(submitted_by_org=org).select_related('listing', 'lead')
        return SyndicationLeadSubmission.objects.none()

    def perform_create(self, serializer):
        user = self.request.user
        org = get_user_org(user)
        lead = serializer.validated_data['lead']
        if org and lead.organization_id != org.pk:
            raise PermissionDenied('Lead does not belong to your organization.')
        listing = serializer.validated_data['listing']
        duplicate = SyndicationLeadSubmission.objects.filter(
            lead=lead, listing=listing,
            status__in=[SyndicationLeadSubmission.Status.PENDING, SyndicationLeadSubmission.Status.ACCEPTED]
        ).exists()
        if duplicate:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': 'An open submission already exists for this lead and listing.'})
        agent = user if _is_agent(user) else None
        serializer.save(submitted_by_org=org, submitted_by_agent=agent)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        submission = self.get_object()
        org = get_user_org(request.user)
        if not org or submission.listing.developer_org_id != org.pk:
            return Response({'detail': 'Only the listing developer can accept submissions.'}, status=status.HTTP_403_FORBIDDEN)
        CommissionLedger().record_submission(submission)
        submission.status = SyndicationLeadSubmission.Status.ACCEPTED
        submission.reviewed_at = timezone.now()
        submission.reviewed_by = request.user
        SyndicationLeadSubmission.objects.filter(pk=submission.pk).update(
            status=SyndicationLeadSubmission.Status.ACCEPTED,
            reviewed_at=submission.reviewed_at,
            reviewed_by=request.user,
        )
        submission.refresh_from_db()
        return Response(SyndicationLeadSubmissionSerializer(submission).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        submission = self.get_object()
        org = get_user_org(request.user)
        if not org or submission.listing.developer_org_id != org.pk:
            return Response({'detail': 'Only the listing developer can reject submissions.'}, status=status.HTTP_403_FORBIDDEN)
        submission.status = SyndicationLeadSubmission.Status.REJECTED
        submission.reviewed_at = timezone.now()
        submission.reviewed_by = request.user
        SyndicationLeadSubmission.objects.filter(pk=submission.pk).update(
            status=SyndicationLeadSubmission.Status.REJECTED,
            reviewed_at=submission.reviewed_at,
            reviewed_by=request.user,
        )
        submission.refresh_from_db()
        return Response(SyndicationLeadSubmissionSerializer(submission).data)
