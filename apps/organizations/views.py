from apps.compliance.parsers import MaskedFormParser, MaskedMultiPartParser
from apps.properties.taxonomy import property_type_expression  # T-1070g
import datetime
import logging

from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import JSONParser

from .models import Organization, OrganizationConfig, OrganizationInvite, OrganizationMembership
from .serializers import (
    OrgInviteResponseSerializer, OrgInviteSerializer, OrgMemberInviteSerializer, OrgMemberSerializer, OrgMemberUpdateSerializer,
    OwnershipTransferRequestSerializer, OwnershipTransferResponseSerializer,
    OrganizationListSerializer, OrganizationDetailSerializer,
    OrgRegistrationSerializer, OrgRegistrationOTPVerifySerializer,
)
from apps.core.permissions import IsAdminUser, IsAdminOrOrgAdmin
from drf_spectacular.utils import extend_schema
from .services import OrgConfigService

logger = logging.getLogger(__name__)


class OrganizationListView(APIView):
    """
    GET  /api/v1/organizations/  — list all (admin) or own org (developer)
    POST /api/v1/organizations/  — create new org (admin only)
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MaskedMultiPartParser, MaskedFormParser, JSONParser]

    def get(self, request):
        role = request.user.role
        if role == 'admin':
            qs = Organization.objects.all()
        elif role == 'developer':
            # T-1032b: every org the developer owns (owner membership is canonical).
            from .membership import owned_organization_ids
            qs = Organization.objects.filter(pk__in=owned_organization_ids(request.user))
        else:
            return Response(status=status.HTTP_403_FORBIDDEN)

        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(name__icontains=search)

        is_active = request.query_params.get('is_active')
        if is_active == 'true':
            qs = qs.filter(is_active=True)
        elif is_active == 'false':
            qs = qs.filter(is_active=False)

        qs = qs.order_by('-created_at')

        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        serializer = OrganizationListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        if request.user.role != 'admin':
            return Response(
                {'detail': 'Only platform admins can create organizations.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = OrganizationDetailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = serializer.save()
        logger.info(f"Organization created: {org.id} '{org.name}' by admin {request.user.id}")
        return Response(OrganizationDetailSerializer(org).data, status=status.HTTP_201_CREATED)


class OrganizationDetailView(APIView):
    """
    GET    /api/v1/organizations/<id>/  — detail
    PATCH  /api/v1/organizations/<id>/  — update (admin or org admin)
    DELETE /api/v1/organizations/<id>/  — delete (admin only)
    """
    permission_classes = [IsAuthenticated, IsAdminOrOrgAdmin]
    parser_classes     = [MaskedMultiPartParser, MaskedFormParser, JSONParser]

    def _get_org(self, pk, user):
        try:
            org = Organization.objects.get(pk=pk)
        except Organization.DoesNotExist:
            return None, Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Object-level permission: admin sees all, developer sees own only
        from .membership import owned_organization_ids
        if user.role == 'developer' and org.pk not in owned_organization_ids(user):
            return None, Response(status=status.HTTP_403_FORBIDDEN)

        return org, None

    def get(self, request, pk):
        org, err = self._get_org(pk, request.user)
        if err:
            return err
        return Response(OrganizationDetailSerializer(org).data)

    def patch(self, request, pk):
        org, err = self._get_org(pk, request.user)
        if err:
            return err

        # Only admin can change admin_user or is_verified
        restricted = {'admin_user', 'is_verified'}
        if request.user.role != 'admin' and restricted & set(request.data.keys()):
            return Response(
                {'detail': 'Only platform admins can change admin_user or is_verified.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = OrganizationDetailSerializer(org, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info(f"Organization updated: {org.id} by user {request.user.id}")
        return Response(OrganizationDetailSerializer(org).data)

    def delete(self, request, pk):
        if request.user.role != 'admin':
            return Response(
                {'detail': 'Only platform admins can delete organizations.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        org, err = self._get_org(pk, request.user)
        if err:
            return err
        org_name = org.name
        org.delete()
        logger.info(f"Organization deleted: '{org_name}' by admin {request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationMeView(APIView):
    """
    GET  /api/v1/organizations/me/  — returns the calling developer's organization
    PATCH /api/v1/organizations/me/ — update own organization
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MaskedMultiPartParser, MaskedFormParser, JSONParser]

    def _get_own_org(self, user):
        # T-1032b: the active org if the caller owns it, else their first owned org.
        from apps.core.permissions import get_active_membership
        from .membership import owned_organization_ids
        owned = owned_organization_ids(user)
        active = get_active_membership(user)
        org_id = active.organization_id if active and active.organization_id in owned else (
            owned[0] if owned else None)
        org = Organization.objects.filter(pk=org_id).first() if org_id else None
        if org is not None:
            return org, None
        return None, Response(
            {'detail': 'No organization linked to your account.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    def get(self, request):
        if request.user.role not in ('admin', 'developer'):
            return Response(status=status.HTTP_403_FORBIDDEN)
        org, err = self._get_own_org(request.user)
        if err:
            return err
        return Response(OrganizationDetailSerializer(org).data)

    def patch(self, request, *args, **kwargs):
        if request.user.role not in ('admin', 'developer'):
            return Response(status=status.HTTP_403_FORBIDDEN)
        org, err = self._get_own_org(request.user)
        if err:
            return err

        # Non-admin developers cannot change admin_user or verification status
        restricted = {'admin_user', 'is_verified'}
        if request.user.role != 'admin' and restricted & set(request.data.keys()):
            return Response(
                {'detail': 'Only platform admins can change admin_user or is_verified.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = OrganizationDetailSerializer(org, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(OrganizationDetailSerializer(org).data)


class OrgConfigView(APIView):
    """
    GET  /api/v1/organizations/me/config/  — org feature flags (org overrides + platform fallback)
    PATCH /api/v1/organizations/me/config/ — set org-level feature flag overrides
    DELETE /api/v1/organizations/me/config/<key>/ — reset key to platform default
    """
    permission_classes = [IsAuthenticated]

    def _get_org(self, user):
        if user.role not in ('admin', 'developer'):
            return None, Response(status=status.HTTP_403_FORBIDDEN)
        from apps.core.permissions import get_admin_org
        org = get_admin_org(user)
        if org is None:
            return None, Response(
                {'detail': 'No organization linked to your account.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return org, None

    def get(self, request):
        org, err = self._get_org(request.user)
        if err:
            return err
        features = OrgConfigService.get_features(org)
        overrides = set(
            OrganizationConfig.objects.filter(organization=org).values_list('key', flat=True)
        )
        return Response({
            'features': features,
            'overrides': list(overrides),
            'allowed_keys': sorted(OrganizationConfig.ALLOWED_KEYS),
        })

    def patch(self, request):
        org, err = self._get_org(request.user)
        if err:
            return err

        invalid = set(request.data.keys()) - OrganizationConfig.ALLOWED_KEYS
        if invalid:
            return Response(
                {'detail': f"Invalid keys: {sorted(invalid)}. Allowed: {sorted(OrganizationConfig.ALLOWED_KEYS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for key, raw_value in request.data.items():
            value = 'true' if str(raw_value).lower() in ('true', '1', 'yes') else 'false'
            OrgConfigService.set(org, key, value, user=request.user)

        features = OrgConfigService.get_features(org)
        return Response({'features': features})

    def delete(self, request, key):
        org, err = self._get_org(request.user)
        if err:
            return err
        if key not in OrganizationConfig.ALLOWED_KEYS:
            return Response(
                {'detail': f"'{key}' is not an overridable key."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        OrgConfigService.reset(org, key)
        return Response({'detail': f"'{key}' reset to platform default."})


class AdminOrgConfigView(APIView):
    """
    GET/PATCH /api/v1/organizations/{pk}/config/
    DELETE    /api/v1/organizations/{pk}/config/{key}/
    Admin-only: read and override feature flags for any org.
    """
    permission_classes = [IsAdminUser]

    def _get_org(self, pk):
        try:
            return Organization.objects.get(pk=pk), None
        except Organization.DoesNotExist:
            return None, Response({'detail': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)

    def get(self, request, pk, key=None):
        org, err = self._get_org(pk)
        if err:
            return err
        features = OrgConfigService.get_features(org)
        overrides = set(
            OrganizationConfig.objects.filter(organization=org).values_list('key', flat=True)
        )
        return Response({
            'features': features,
            'overrides': list(overrides),
            'allowed_keys': sorted(OrganizationConfig.ALLOWED_KEYS),
        })

    def patch(self, request, pk, key=None):
        org, err = self._get_org(pk)
        if err:
            return err
        invalid = set(request.data.keys()) - OrganizationConfig.ALLOWED_KEYS
        if invalid:
            return Response(
                {'detail': f"Invalid keys: {sorted(invalid)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        for k, raw_value in request.data.items():
            value = 'true' if str(raw_value).lower() in ('true', '1', 'yes') else 'false'
            OrgConfigService.set(org, k, value, user=request.user)
        features = OrgConfigService.get_features(org)
        return Response({'features': features})

    def delete(self, request, pk, key):
        org, err = self._get_org(pk)
        if err:
            return err
        if key not in OrganizationConfig.ALLOWED_KEYS:
            return Response(
                {'detail': f"'{key}' is not an overridable key."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        OrgConfigService.reset(org, key)
        return Response({'detail': f"'{key}' reset to platform default."})


class OrgDashboardView(APIView):
    """
    GET /api/v1/organizations/me/dashboard/
    Overview stats for the org admin's dashboard.
    Scoped strictly to the calling user's organization.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ('admin', 'developer'):
            return Response(status=status.HTTP_403_FORBIDDEN)

        from apps.core.permissions import get_admin_org
        org = get_admin_org(request.user)
        if org is None:
            return Response({'detail': 'No organization linked.'}, status=status.HTTP_404_NOT_FOUND)

        from apps.leads.models import Lead
        from apps.agents.models import Agent
        from apps.properties.models import Property

        leads      = Lead.objects.filter(organization=org)
        agents     = Agent.objects.filter(organization=org, is_active=True)
        properties = Property.objects.filter(organization=org, is_active=True)

        now      = timezone.now()
        week_ago = now - datetime.timedelta(days=7)

        total_leads     = leads.count()
        hot_leads       = leads.filter(score__gte=70).count()
        new_this_week   = leads.filter(created_at__gte=week_ago).count()
        routing_queue   = leads.filter(routing_state='org_queue').count()
        active_agents   = agents.count()
        pending_agents  = Agent.objects.filter(organization=org, registration_status='pending').count()
        total_inventory = properties.count()
        verified_props  = properties.filter(legal_status='verified').count()
        avg_ai_score    = properties.aggregate(avg=Avg('ai_score'))['avg'] or 0

        by_status = dict(
            leads.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
        )
        by_intent = dict(
            leads.values_list('intent').annotate(c=Count('id')).values_list('intent', 'c')
        )
        by_prop_type = dict(
            properties.annotate(ptype=property_type_expression()).values_list('ptype').annotate(c=Count('id')).values_list('ptype', 'c')
        )

        return Response({
            'leads': {
                'total':          total_leads,
                'hot':            hot_leads,
                'new_this_week':  new_this_week,
                'routing_queue':  routing_queue,
                'by_status':      by_status,
                'by_intent':      by_intent,
            },
            'agents': {
                'active':  active_agents,
                'pending': pending_agents,
            },
            'inventory': {
                'total':         total_inventory,
                'verified':      verified_props,
                'avg_ai_score':  round(avg_ai_score, 1),
                'by_type':       by_prop_type,
            },
        })


class OrgAIStatsView(APIView):
    """
    GET /api/v1/organizations/me/ai-stats/
    AI performance metrics for the org: routing queue depth, hot lead identification,
    chat success rate, and a snapshot of recent AI-handled conversations.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ('admin', 'developer'):
            return Response(status=status.HTTP_403_FORBIDDEN)

        from apps.core.permissions import get_admin_org
        org = get_admin_org(request.user)
        if org is None:
            return Response({'detail': 'No organization linked.'}, status=status.HTTP_404_NOT_FOUND)

        from apps.leads.models import Lead, ConversationMessage

        now      = timezone.now()
        week_ago = now - datetime.timedelta(days=7)

        leads = Lead.objects.filter(organization=org)

        total_leads     = leads.count()
        hot_leads       = leads.filter(score__gte=70).count()
        routing_queue   = leads.filter(routing_state='org_queue').count()
        agent_assigned  = leads.filter(routing_state='agent_assigned').count()
        qualified_leads = leads.filter(status='qualified').count()

        # Leads that have at least one inbound WhatsApp message
        leads_with_convos = leads.filter(
            messages__direction='inbound'
        ).distinct().count()

        # Chat success rate: qualified or hot leads with conversations / all leads with conversations
        converted_with_convos = leads.filter(
            Q(status='qualified') | Q(score__gte=70),
            messages__direction='inbound',
        ).distinct().count()

        chat_success_rate = (
            round(converted_with_convos / leads_with_convos * 100, 1)
            if leads_with_convos > 0 else 0.0
        )

        new_this_week = leads.filter(created_at__gte=week_ago).count()

        # Recent conversation snapshot: last 15 inbound messages across org leads
        recent_messages = (
            ConversationMessage.objects
            .filter(Q(enquiry__isnull=True) | Q(enquiry__organization=org),
                    lead__organization=org, direction='inbound')
            .select_related('lead__user')
            .order_by('-created_at')[:15]
        )

        from apps.leads.privacy import phone as lead_phone
        conversations = [
            {
                'lead_id':    str(m.lead_id),
                'lead_phone': lead_phone(m.lead),
                'lead_name':  getattr(m.lead.user, 'name', None),
                'lead_score': m.lead.score,
                'lead_status': m.lead.status,
                'message_preview': m.body[:120],
                'channel':    m.channel,
                'created_at': m.created_at.isoformat(),
            }
            for m in recent_messages
        ]

        return Response({
            'summary': {
                'total_leads':        total_leads,
                'hot_leads':          hot_leads,
                'routing_queue':      routing_queue,
                'agent_assigned':     agent_assigned,
                'qualified_leads':    qualified_leads,
                'new_this_week':      new_this_week,
                'leads_with_convos':  leads_with_convos,
                'chat_success_rate':  chat_success_rate,
            },
            'recent_conversations': conversations,
        })


class OrganizationSuspendView(APIView):
    """POST /api/v1/organizations/{id}/suspend/ — admin only."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, pk):
        try:
            org = Organization.objects.get(pk=pk)
        except Organization.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not org.is_active:
            return Response(
                {'detail': 'Organization is already suspended.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org.is_active = False
        org.save(update_fields=['is_active'])
        logger.info(f"Organization suspended: {org.id} '{org.name}' by admin {request.user.id}")
        return Response(OrganizationDetailSerializer(org).data)


class OrganizationActivateView(APIView):
    """POST /api/v1/organizations/{id}/activate/ — admin only."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, pk):
        try:
            org = Organization.objects.get(pk=pk)
        except Organization.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if org.is_active:
            return Response(
                {'detail': 'Organization is already active.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org.is_active = True
        org.save(update_fields=['is_active'])
        logger.info(f"Organization activated: {org.id} '{org.name}' by admin {request.user.id}")
        return Response(OrganizationDetailSerializer(org).data)


class OrgRegistrationView(APIView):
    """
    POST /api/v1/organizations/register/
    Public self-service endpoint — no auth required.

    Creates User(role=developer) + Organization(plan=trial, is_verified=True)
    + OrganizationMembership(role=owner), then issues an OTP for phone
    verification.  Returns {otp_required: true, phone} so the client can
    present the OTP entry screen.
    """
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        from rest_framework.permissions import AllowAny
        serializer = OrgRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        from django.db import transaction as db_transaction
        from apps.users.models import User
        from apps.users.services import OTPService

        with db_transaction.atomic():
            user = User.objects.create_user(
                phone=d['phone'],
                email=d['email'],
                password=d['password'],
                name=d['admin_name'],
                role=User.Role.DEVELOPER,
                is_active=True,
                is_phone_verified=False,
            )
            org = Organization.objects.create(
                name=d['org_name'],
                org_type=d['org_type'],
                country=d['country'],
                plan=Organization.Plan.TRIAL,
                is_verified=True,
            )
            # T-1032b: ownership is the owner membership; admin_user follows.
            from .membership import set_owner
            set_owner(org, user)

        try:
            otp = OTPService.issue(d['phone'], purpose='org_registration')
            from apps.notifications.delivery import enqueue_otp
            enqueue_otp(otp)
        except Exception:
            logger.warning("OTP dispatch failed for org registration phone %s", d['phone'])

        logger.info("Org registration initiated: org=%s user=%s", org.id, user.id)
        return Response(
            {'otp_required': True, 'phone': d['phone']},
            status=status.HTTP_201_CREATED,
        )


class OrgRegistrationOTPVerifyView(APIView):
    """
    POST /api/v1/organizations/register/verify-otp/
    Public — no auth required.

    Verifies the OTP issued during org registration, marks the user's phone as
    verified, and issues httpOnly auth cookies so the user lands directly in
    their org dashboard without a separate login step.
    """
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        serializer = OrgRegistrationOTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']
        code  = serializer.validated_data['code']

        from apps.users.services import OTPService
        from apps.users.models import User
        try:
            OTPService.verify(phone, code, purpose='org_registration')
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(phone=phone).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_400_BAD_REQUEST)

        user.is_phone_verified = True
        user.save(update_fields=['is_phone_verified'])

        from apps.users.mfa import login_response       # T-1011: owners need MFA
        from apps.users.serializers import UserSerializer
        logger.info("Org registration OTP verified, user auto-logged in: user=%s", user.id)
        return login_response(user, UserSerializer(user).data)


def _member_payload(m, user=None):
    user = user or m.user
    return {
        'id': str(m.id),
        'user_phone': user.phone,
        'user_name': getattr(user, 'name', None),
        'role': m.role,
        'employment_type': m.employment_type,
        'is_active': m.is_active,
        'joined_at': m.joined_at.isoformat(),
    }


class _OrgMembersBase(APIView):
    """Policy and audit live in ``apps.organizations.membership`` (ADR-020/021)."""
    permission_classes = [IsAuthenticated, IsAdminOrOrgAdmin]

    def _get_org(self, user):
        from apps.core.permissions import get_user_org
        org = get_user_org(user)
        if org is None:
            raise PermissionDenied('No organization found for this account.')
        return org

    def _get_membership(self, org, membership_id):
        try:
            return OrganizationMembership.objects.select_related('user').get(
                id=membership_id, organization=org, is_active=True)
        except OrganizationMembership.DoesNotExist:
            raise NotFound('Membership not found.')



class OrgMembershipView(_OrgMembersBase):
    """
    GET  /api/v1/organizations/me/members/ — list active members of caller's org
    POST /api/v1/organizations/me/members/ — invite an existing user into the org
    """

    @extend_schema(responses=OrgMemberSerializer(many=True))
    def get(self, request):
        org = self._get_org(request.user)
        members = OrganizationMembership.objects.filter(
            organization=org, is_active=True
        ).select_related('user').order_by('joined_at')
        return Response([_member_payload(m) for m in members])

    @extend_schema(request=OrgMemberInviteSerializer, responses={201: OrgInviteSerializer})
    def post(self, request):
        """Creates a pending invitation; the user joins only after accepting (KI-041)."""
        from apps.users.models import User
        from .membership import check_invite, create_invite
        org = self._get_org(request.user)
        phone = str(request.data.get('phone', '')).strip()
        role = request.data.get('role', OrganizationMembership.Role.AGENT)
        employment_type = request.data.get('employment_type', OrganizationMembership.EmploymentType.INTERNAL)

        if not phone:
            return Response({'detail': 'phone is required.'}, status=status.HTTP_400_BAD_REQUEST)
        check_invite(request.user, org, role, employment_type)

        user = User.objects.filter(phone=phone).first()
        if not user:
            return Response({'detail': 'No user found with this phone number.'}, status=status.HTTP_404_NOT_FOUND)

        invite = create_invite(request.user, org, user, role, employment_type, request=request)
        return Response(OrgInviteSerializer(invite).data, status=status.HTTP_201_CREATED)


class OrgInviteListView(_OrgMembersBase):
    """GET /api/v1/organizations/me/invites/ — open invitations of the caller's org."""

    @extend_schema(responses=OrgInviteSerializer(many=True))
    def get(self, request):
        from django.utils import timezone as _tz
        from .membership import _MANAGER_ROLES, actor_role
        org = self._get_org(request.user)
        if actor_role(request.user, org.pk) not in _MANAGER_ROLES:
            raise PermissionDenied('Only organization admins can view invitations.')
        qs = (OrganizationInvite.objects.filter(organization=org, status='pending',
                                                expires_at__gt=_tz.now())
              .select_related('organization', 'invitee', 'invited_by'))
        return Response(OrgInviteSerializer(qs, many=True).data)


class OrgInviteRevokeView(_OrgMembersBase):
    """DELETE /api/v1/organizations/me/invites/{id}/ — withdraw an open invitation."""

    @extend_schema(responses={204: None})
    def delete(self, request, invite_id):
        from .membership import revoke_invite
        revoke_invite(request.user, self._get_org(request.user), invite_id, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyInvitesView(APIView):
    """GET /api/v1/organizations/invites/ — the caller's open invitations."""
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OrgInviteSerializer(many=True))
    def get(self, request):
        from django.utils import timezone as _tz
        qs = (OrganizationInvite.objects.filter(invitee=request.user, status='pending',
                                                expires_at__gt=_tz.now())
              .select_related('organization', 'invitee', 'invited_by'))
        return Response(OrgInviteSerializer(qs, many=True).data)


class InviteRespondView(APIView):
    """
    POST /api/v1/organizations/invites/{id}/accept/
    POST /api/v1/organizations/invites/{id}/decline/
    Only the invitee can respond; another user's invite id → 404.
    """
    permission_classes = [IsAuthenticated]
    accept = True

    @extend_schema(request=None, responses=OrgInviteResponseSerializer)
    def post(self, request, invite_id):
        from .membership import respond_to_invite
        invite, membership = respond_to_invite(request.user, invite_id, accept=self.accept,
                                               request=request)
        if self.accept and membership is None:
            return Response({'detail': 'This invitation is no longer valid.',
                             'invite': OrgInviteSerializer(invite).data},
                            status=status.HTTP_409_CONFLICT)
        return Response({'invite': OrgInviteSerializer(invite).data,
                         'membership': _member_payload(membership) if membership else None})



class OrgMembershipDetailView(_OrgMembersBase):
    """
    PATCH  /api/v1/organizations/me/members/{id}/ — change a member's role / employment type
    DELETE /api/v1/organizations/me/members/{id}/ — deactivate a membership
    """

    @extend_schema(request=OrgMemberUpdateSerializer, responses=OrgMemberSerializer)
    def patch(self, request, membership_id):
        from .membership import change_role
        org = self._get_org(request.user)
        membership = self._get_membership(org, membership_id)
        role = request.data.get('role')
        if not role:
            return Response({'detail': 'role is required.'}, status=status.HTTP_400_BAD_REQUEST)
        membership = change_role(request.user, membership, role,
                                 request.data.get('employment_type') or None, request=request)
        return Response(_member_payload(membership))

    @extend_schema(responses={204: None})
    def delete(self, request, membership_id):
        from .membership import remove_member
        org = self._get_org(request.user)
        membership = self._get_membership(org, membership_id)
        remove_member(membership, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrgOwnershipTransferView(APIView):
    """
    POST /api/v1/organizations/me/ownership/transfer/   — owner of the active org
    POST /api/v1/organizations/{pk}/ownership/transfer/ — platform admin

    Body: ``{"membership_id": <int>}`` — an active ``org_admin`` of the org.
    The previous owner stays as ``org_admin`` (ADR-021).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(request=OwnershipTransferRequestSerializer,
                   responses=OwnershipTransferResponseSerializer)
    def post(self, request, pk=None):
        from apps.core.permissions import get_admin_org
        from .membership import transfer_ownership
        if pk is not None:
            if request.user.role != 'admin':
                raise PermissionDenied()
            try:
                org = Organization.objects.get(pk=pk)
            except Organization.DoesNotExist:
                raise NotFound('Organization not found.')
        else:
            org = get_admin_org(request.user)
            if org is None:
                raise PermissionDenied('Only the organization owner can transfer ownership.')

        raw_id = request.data.get('membership_id')
        try:
            membership_id = int(raw_id)
        except (TypeError, ValueError):
            return Response({'detail': 'membership_id must be an integer.'},
                            status=status.HTTP_400_BAD_REQUEST)

        owner = transfer_ownership(request.user, org, membership_id, request=request)
        return Response({
            'organization_id': str(org.pk),
            'owner': _member_payload(owner),
        })
