import logging
from django.db import transaction
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.core.permissions import IsAdminOrDeveloper
from apps.organizations.models import Organization
from .models import Agent
from .serializers import AgentSerializer, AgentRegistrationSerializer

logger = logging.getLogger(__name__)


def _get_developer_org(user) -> Organization:
    """Return the Organization owned by this developer user, or raise NotFound."""
    try:
        return user.owned_organization
    except Organization.DoesNotExist:
        raise NotFound("No organization linked to this account.")


class AgentMeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /agents/me/ — agent's own profile."""
    serializer_class   = AgentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        try:
            return self.request.user.agent_profile
        except Agent.DoesNotExist:
            raise NotFound("No agent profile linked to this account.")


class AgentAdminSerializer(AgentSerializer):
    """Extends AgentSerializer to allow admins to write all status + identity fields."""
    class Meta(AgentSerializer.Meta):
        read_only_fields = (
            'id', 'total_leads', 'total_listings',
            'closed_deals', 'user_phone', 'user_email',
            'joined_at', 'updated_at',
        )


class AgentListView(generics.ListCreateAPIView):
    """
    GET  /agents/           — admin: all agents; developer: own org agents.
    GET  /agents/?status=pending — pending applications queue.
    POST /agents/           — admin: full approved create; developer: creates pending agent in own org.
    """
    serializer_class   = AgentAdminSerializer
    permission_classes = [IsAdminOrDeveloper]

    def get_queryset(self):
        role = self.request.user.role
        base = Agent.objects.select_related('user', 'organization')

        reg_status = self.request.query_params.get('status')

        if role == 'admin':
            qs = base.all()
            if reg_status:
                qs = qs.filter(registration_status=reg_status)
            return qs

        if role == 'developer':
            org = _get_developer_org(self.request.user)
            qs = base.filter(organization=org)
            if reg_status:
                qs = qs.filter(registration_status=reg_status)
            return qs

        return Agent.objects.none()

    def perform_create(self, serializer):
        if self.request.user.role == 'admin':
            from apps.users.models import User
            phone = serializer.validated_data.get('phone', '')
            name  = serializer.validated_data.get('name', '')
            with transaction.atomic():
                user, created = User.objects.get_or_create(
                    phone=phone,
                    defaults={'name': name, 'role': User.Role.AGENT},
                )
                if not created and user.role != User.Role.AGENT:
                    user.role = User.Role.AGENT
                    user.save(update_fields=['role'])
                serializer.save(
                    user=user,
                    registration_status=Agent.RegistrationStatus.APPROVED,
                    is_active=True,
                    is_verified=True,
                )
        else:
            # Developer creates an agent scoped to their own org, pending approval.
            org = _get_developer_org(self.request.user)
            serializer.save(
                organization=org,
                employment_type=Agent.EmploymentType.INTERNAL,
                registration_status=Agent.RegistrationStatus.PENDING,
                is_active=False,
                is_verified=False,
            )


class AgentAdminDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /agents/{id}/
    Admin: full access to any agent.
    Developer: GET/PATCH only for agents in their own org. DELETE is admin-only.
    """
    serializer_class   = AgentAdminSerializer
    permission_classes = [IsAdminOrDeveloper]
    queryset           = Agent.objects.select_related('user', 'organization').all()

    def get_object(self):
        role = self.request.user.role
        obj = super().get_object()
        if role == 'admin':
            return obj
        # Developer: verify the agent belongs to their org.
        org = _get_developer_org(self.request.user)
        if obj.organization_id != org.id:
            raise PermissionDenied("You can only access agents in your own organisation.")
        return obj

    def destroy(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response(
                {'detail': 'Only admins can delete agent records.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)


class AgentRegisterView(APIView):
    """POST /agents/register/ — public self-registration."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AgentRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = serializer.save()
        self._notify_approvers(agent)
        return Response(
            {
                'detail': 'Registration submitted. You will be notified once your application is reviewed.',
                'agent_id': agent.id,
            },
            status=status.HTTP_201_CREATED,
        )

    def _notify_approvers(self, agent: Agent):
        try:
            from apps.notifications.services import notify_user
            from apps.users.models import User

            if agent.organization and agent.organization.admin_user:
                notify_user(
                    agent.organization.admin_user,
                    title='New Agent Application',
                    message=(
                        f"{agent.name} ({agent.phone}) has applied to join your team "
                        f"as a {agent.get_agent_type_display()}. "
                        f"Please review their application in your dashboard."
                    ),
                )

            admins = User.objects.filter(role=User.Role.ADMIN, is_active=True)
            org_label = (
                f"under {agent.organization.name}"
                if agent.organization
                else "as an independent agent"
            )
            for admin in admins:
                notify_user(
                    admin,
                    title='New Agent Application',
                    message=(
                        f"{agent.name} ({agent.phone}) has registered {org_label}. "
                        f"Review and approve in the Agents dashboard."
                    ),
                )
        except Exception as exc:
            logger.warning(f"Failed to notify approvers for agent {agent.id}: {exc}")


class AgentApproveView(APIView):
    """POST /agents/{id}/approve/ — admin always; developer only for their org's applicants."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        agent = self._get_approvable_agent(request, pk)

        if agent.registration_status == Agent.RegistrationStatus.APPROVED:
            return Response({'detail': 'Agent is already approved.'}, status=status.HTTP_400_BAD_REQUEST)

        # TRIAL plan gate — max 2 active agents per org
        if (
            request.user.role == 'developer'
            and agent.organization is not None
            and agent.organization.plan == 'trial'
        ):
            active_count = Agent.objects.filter(
                organization=agent.organization,
                registration_status=Agent.RegistrationStatus.APPROVED,
                is_active=True,
            ).count()
            if active_count >= 2:
                return Response(
                    {'detail': 'Trial plan limit reached (2 agents). Upgrade your plan to add more team members.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        agent.registration_status = Agent.RegistrationStatus.APPROVED
        agent.is_verified = True
        agent.is_active   = True
        agent.verified_at = timezone.now()
        agent.verified_by = request.user
        agent.rejection_reason = ''
        agent.save(update_fields=[
            'registration_status', 'is_verified', 'is_active',
            'verified_at', 'verified_by', 'rejection_reason', 'updated_at',
        ])

        if agent.user:
            agent.user.is_active = True
            agent.user.save(update_fields=['is_active'])
            self._notify_agent(agent, approved=True)

        return Response({'detail': 'Agent approved successfully.'})

    def _get_approvable_agent(self, request, pk):
        try:
            agent = Agent.objects.select_related('user', 'organization').get(pk=pk)
        except Agent.DoesNotExist:
            raise NotFound("Agent not found.")

        role = request.user.role
        if role == 'admin':
            return agent

        if role == 'developer':
            org = _get_developer_org(request.user)
            if agent.organization_id != org.id:
                raise PermissionDenied("You can only approve agents who applied to your organization.")
            return agent

        raise PermissionDenied("Admin or developer access required.")

    def _notify_agent(self, agent: Agent, approved: bool):
        try:
            from apps.notifications.services import notify_user
            if approved:
                notify_user(
                    agent.user,
                    title='Application Approved',
                    message=(
                        "Your agent registration has been approved! "
                        "You can now log in to the RealTron AI dashboard."
                    ),
                )
        except Exception as exc:
            logger.warning(f"Failed to notify agent {agent.id} on approval: {exc}")


class AgentRejectView(APIView):
    """POST /agents/{id}/reject/ — admin always; developer only for their org's applicants."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        reason = request.data.get('rejection_reason', '').strip()
        if not reason:
            return Response({'detail': 'rejection_reason is required.'}, status=status.HTTP_400_BAD_REQUEST)

        agent = self._get_rejectable_agent(request, pk)

        if agent.registration_status == Agent.RegistrationStatus.REJECTED:
            return Response({'detail': 'Agent application is already rejected.'}, status=status.HTTP_400_BAD_REQUEST)

        agent.registration_status = Agent.RegistrationStatus.REJECTED
        agent.rejection_reason    = reason
        agent.is_verified = False
        agent.is_active   = False
        agent.save(update_fields=[
            'registration_status', 'rejection_reason',
            'is_verified', 'is_active', 'updated_at',
        ])

        if agent.user:
            agent.user.is_active = False
            agent.user.save(update_fields=['is_active'])
            self._notify_agent(agent, reason)

        return Response({'detail': 'Agent application rejected.'})

    def _get_rejectable_agent(self, request, pk):
        try:
            agent = Agent.objects.select_related('user', 'organization').get(pk=pk)
        except Agent.DoesNotExist:
            raise NotFound("Agent not found.")

        role = request.user.role
        if role == 'admin':
            return agent

        if role == 'developer':
            org = _get_developer_org(request.user)
            if agent.organization_id != org.id:
                raise PermissionDenied("You can only reject agents who applied to your organization.")
            return agent

        raise PermissionDenied("Admin or developer access required.")

    def _notify_agent(self, agent: Agent, reason: str):
        try:
            from apps.notifications.services import notify_user
            notify_user(
                agent.user,
                title='Application Not Approved',
                message=(
                    f"Your agent registration was not approved. "
                    f"Reason: {reason}. "
                    f"Please contact support if you believe this is an error."
                ),
            )
        except Exception as exc:
            logger.warning(f"Failed to notify agent {agent.id} on rejection: {exc}")


class AgentAvailabilityView(APIView):
    """
    PATCH /agents/me/availability/  — agent sets own availability.
    PATCH /agents/{pk}/availability/ — admin sets any agent's availability.
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk=None):
        new_status = request.data.get('availability_status', '').strip()
        if new_status not in Agent.AvailabilityStatus.values:
            return Response(
                {'detail': f"availability_status must be one of: {Agent.AvailabilityStatus.values}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if pk is None:
            # Agent updating their own status
            try:
                agent = request.user.agent_profile
            except Agent.DoesNotExist:
                raise NotFound("No agent profile linked to this account.")
        else:
            # Admin updating any agent
            if request.user.role != 'admin':
                raise PermissionDenied("Admin access required.")
            try:
                agent = Agent.objects.get(pk=pk)
            except Agent.DoesNotExist:
                raise NotFound("Agent not found.")

        agent.availability_status = new_status
        agent.save(update_fields=['availability_status', 'updated_at'])
        return Response({'availability_status': agent.availability_status})


class AgentAvailableListView(generics.ListAPIView):
    """GET /agents/available/?city=Lahore — active, available agents for a city."""
    serializer_class   = AgentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Agent.objects.filter(
            is_active=True,
            registration_status=Agent.RegistrationStatus.APPROVED,
            availability_status=Agent.AvailabilityStatus.AVAILABLE,
        ).select_related('user')
        city = self.request.query_params.get('city', '').strip()
        if city:
            qs = qs.filter(cities__icontains=city)
        return qs


class TeamView(APIView):
    """
    GET  /agents/team/  — developer sees their approved team members.
    POST /agents/team/  — developer adds an existing agent to their org.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role not in ('admin', 'developer'):
            raise PermissionDenied("Developer or admin access required.")
        org = _get_developer_org(request.user)
        members = Agent.objects.filter(
            organization=org,
            registration_status=Agent.RegistrationStatus.APPROVED,
        ).select_related('user')
        return Response(AgentSerializer(members, many=True).data)

    def post(self, request):
        if request.user.role not in ('admin', 'developer'):
            raise PermissionDenied("Developer or admin access required.")
        org = _get_developer_org(request.user)
        agent_id = request.data.get('agent_id')
        if not agent_id:
            return Response({'detail': 'agent_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            agent = Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist:
            return Response({'detail': 'Agent not found.'}, status=status.HTTP_404_NOT_FOUND)

        # TRIAL plan gate — max 2 active agents per org
        if org.plan == 'trial':
            active_count = Agent.objects.filter(
                organization=org,
                registration_status=Agent.RegistrationStatus.APPROVED,
                is_active=True,
            ).count()
            if active_count >= 2:
                return Response(
                    {'detail': 'Trial plan limit reached (2 agents). Upgrade your plan to add more team members.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        agent.organization    = org
        agent.employment_type = Agent.EmploymentType.INTERNAL
        agent.save(update_fields=['organization', 'employment_type', 'updated_at'])
        return Response(AgentSerializer(agent).data, status=status.HTTP_200_OK)


class TeamMemberView(APIView):
    """DELETE /agents/team/<agent_id>/ — remove agent from org."""
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, agent_id):
        if request.user.role not in ('admin', 'developer'):
            raise PermissionDenied("Developer or admin access required.")
        org = _get_developer_org(request.user)

        try:
            agent = Agent.objects.get(id=agent_id, organization=org)
        except Agent.DoesNotExist:
            return Response({'detail': 'Agent not found in your team.'}, status=status.HTTP_404_NOT_FOUND)

        agent.organization    = None
        agent.employment_type = Agent.EmploymentType.FREELANCE
        agent.save(update_fields=['organization', 'employment_type', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)
