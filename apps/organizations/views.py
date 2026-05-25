import datetime
import logging

from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import Organization, OrganizationConfig, OrganizationMembership
from .serializers import (
    OrganizationListSerializer, OrganizationDetailSerializer,
    OrgRegistrationSerializer, OrgRegistrationOTPVerifySerializer,
)
from apps.core.permissions import IsAdminUser, IsAdminOrOrgAdmin
from .services import OrgConfigService

logger = logging.getLogger(__name__)


class OrganizationListView(APIView):
    """
    GET  /api/v1/organizations/  — list all (admin) or own org (developer)
    POST /api/v1/organizations/  — create new org (admin only)
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        role = request.user.role
        if role == 'admin':
            qs = Organization.objects.select_related('admin_user').all()
        elif role == 'developer':
            qs = Organization.objects.select_related('admin_user').filter(
                admin_user=request.user
            )
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
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def _get_org(self, pk, user):
        try:
            org = Organization.objects.select_related('admin_user').get(pk=pk)
        except Organization.DoesNotExist:
            return None, Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Object-level permission: admin sees all, developer sees own only
        if user.role == 'developer' and org.admin_user_id != user.id:
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
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def _get_own_org(self, user):
        try:
            return Organization.objects.get(admin_user=user), None
        except Organization.DoesNotExist:
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
        org = getattr(user, 'owned_organization', None)
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

        try:
            org = request.user.owned_organization
        except Organization.DoesNotExist:
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
            properties.values_list('property_type').annotate(c=Count('id')).values_list('property_type', 'c')
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

        try:
            org = request.user.owned_organization
        except Organization.DoesNotExist:
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
            .filter(lead__organization=org, direction='inbound')
            .select_related('lead__user')
            .order_by('-created_at')[:15]
        )

        conversations = [
            {
                'lead_id':    str(m.lead_id),
                'lead_phone': m.lead.user.phone,
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
                admin_user=user,
                plan=Organization.Plan.TRIAL,
                is_verified=True,
            )
            OrganizationMembership.objects.create(
                user=user,
                organization=org,
                role=OrganizationMembership.Role.OWNER,
                is_active=True,
            )

        try:
            otp = OTPService.issue(d['phone'], purpose='org_registration')
            from apps.notifications.tasks import send_otp_async
            send_otp_async.delay(d['phone'], otp.code)
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
        from rest_framework_simplejwt.tokens import RefreshToken
        from apps.users.views import _set_auth_cookies

        try:
            OTPService.verify(phone, code, purpose='org_registration')
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(phone=phone).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_400_BAD_REQUEST)

        user.is_phone_verified = True
        user.save(update_fields=['is_phone_verified'])

        refresh = RefreshToken.for_user(user)
        from apps.users.serializers import UserSerializer
        response = Response(UserSerializer(user).data, status=status.HTTP_200_OK)
        _set_auth_cookies(response, str(refresh.access_token), str(refresh), role=user.role)
        logger.info("Org registration OTP verified, user auto-logged in: user=%s", user.id)
        return response
