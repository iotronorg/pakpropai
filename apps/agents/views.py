import logging
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Agent
from .serializers import AgentSerializer, AgentRegistrationSerializer

logger = logging.getLogger(__name__)


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
            'parent_organization_name', 'joined_at', 'updated_at',
        )


class AgentListView(generics.ListCreateAPIView):
    """
    GET  /agents/           — admin: all agents; developer: own org agents.
    GET  /agents/?status=pending — pending applications queue.
    POST /agents/           — admin only (direct create, bypasses registration flow).
    """
    serializer_class   = AgentAdminSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        role = self.request.user.role
        base = Agent.objects.select_related('user', 'parent_organization')

        reg_status = self.request.query_params.get('status')

        if role == 'admin':
            qs = base.all()
            if reg_status:
                qs = qs.filter(registration_status=reg_status)
            return qs

        if role == 'developer':
            try:
                org = self.request.user.agent_profile
            except Agent.DoesNotExist:
                return Agent.objects.none()
            qs = base.filter(parent_organization=org)
            if reg_status:
                qs = qs.filter(registration_status=reg_status)
            return qs

        return Agent.objects.none()

    def perform_create(self, serializer):
        if self.request.user.role != 'admin':
            raise PermissionDenied("Admin access required.")
        # Direct admin creation — mark as approved immediately.
        serializer.save(
            registration_status=Agent.RegistrationStatus.APPROVED,
            is_active=True,
            is_verified=True,
        )


class AgentAdminDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /agents/{id}/ — admin only."""
    serializer_class   = AgentAdminSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset           = Agent.objects.select_related('user', 'parent_organization').all()

    def get_object(self):
        if self.request.user.role != 'admin':
            raise PermissionDenied("Admin access required.")
        return super().get_object()


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

            if agent.parent_organization and agent.parent_organization.user:
                dev_user = agent.parent_organization.user
                notify_user(
                    dev_user,
                    title='New Agent Application',
                    message=(
                        f"{agent.name} ({agent.phone}) has applied to join your team "
                        f"as a {agent.get_agent_type_display()}. "
                        f"Please review their application in your dashboard."
                    ),
                )

            admins = User.objects.filter(role=User.Role.ADMIN, is_active=True)
            org_label = (
                f"under {agent.parent_organization.name}"
                if agent.parent_organization
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
            agent = Agent.objects.select_related('user', 'parent_organization').get(pk=pk)
        except Agent.DoesNotExist:
            raise NotFound("Agent not found.")

        role = request.user.role
        if role == 'admin':
            return agent

        if role == 'developer':
            try:
                org = request.user.agent_profile
            except Agent.DoesNotExist:
                raise PermissionDenied("No organization profile linked to your account.")
            if agent.parent_organization_id != org.id:
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
                        "You can now log in to the PakProp AI dashboard."
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
            agent = Agent.objects.select_related('user', 'parent_organization').get(pk=pk)
        except Agent.DoesNotExist:
            raise NotFound("Agent not found.")

        role = request.user.role
        if role == 'admin':
            return agent

        if role == 'developer':
            try:
                org = request.user.agent_profile
            except Agent.DoesNotExist:
                raise PermissionDenied("No organization profile linked to your account.")
            if agent.parent_organization_id != org.id:
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


class TeamView(APIView):
    """
    GET  /agents/team/              — developer sees their approved team members.
    POST /agents/team/              — developer adds an existing approved agent.
    """
    permission_classes = [permissions.IsAuthenticated]

    def _get_org(self, request):
        if request.user.role not in ('admin', 'developer'):
            raise PermissionDenied("Developer or admin access required.")
        try:
            return request.user.agent_profile
        except Agent.DoesNotExist:
            raise NotFound("No organization profile linked to this account.")

    def get(self, request):
        org = self._get_org(request)
        members = Agent.objects.filter(
            parent_organization=org,
            registration_status=Agent.RegistrationStatus.APPROVED,
        ).select_related('user')
        return Response(AgentSerializer(members, many=True).data)

    def post(self, request):
        org = self._get_org(request)
        agent_id = request.data.get('agent_id')
        if not agent_id:
            return Response({'detail': 'agent_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            agent = Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist:
            return Response({'detail': 'Agent not found.'}, status=status.HTTP_404_NOT_FOUND)

        if agent.id == org.id:
            return Response(
                {'detail': 'Cannot add the organization itself as a member.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agent.parent_organization = org
        agent.save(update_fields=['parent_organization', 'updated_at'])
        return Response(AgentSerializer(agent).data, status=status.HTTP_200_OK)


class TeamMemberView(APIView):
    """DELETE /agents/team/<agent_id>/ — remove agent from org."""
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, agent_id):
        if request.user.role not in ('admin', 'developer'):
            raise PermissionDenied("Developer or admin access required.")
        try:
            org = request.user.agent_profile
        except Agent.DoesNotExist:
            raise NotFound("No organization profile linked to this account.")

        try:
            agent = Agent.objects.get(id=agent_id, parent_organization=org)
        except Agent.DoesNotExist:
            return Response({'detail': 'Agent not found in your team.'}, status=status.HTTP_404_NOT_FOUND)

        agent.parent_organization = None
        agent.save(update_fields=['parent_organization', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)
