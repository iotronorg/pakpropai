from rest_framework import generics, permissions, serializers as drf_serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Agent
from .serializers import AgentSerializer


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
    GET  /agents/ — admin: all agents; developer: own org's agents.
    POST /agents/ — admin only.
    """
    serializer_class   = AgentAdminSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        role = self.request.user.role
        base = Agent.objects.select_related('user', 'parent_organization')

        if role == 'admin':
            return base.all()

        if role == 'developer':
            # Developers see agents whose parent_organization is their own profile.
            org = getattr(self.request, 'agent_profile', None)
            if org is None:
                return Agent.objects.none()
            return base.filter(parent_organization=org)

        return Agent.objects.none()

    def perform_create(self, serializer):
        if self.request.user.role != 'admin':
            raise PermissionDenied("Admin access required.")
        serializer.save()


class AgentAdminDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /agents/{id}/ — admin only."""
    serializer_class   = AgentAdminSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset           = Agent.objects.select_related('user', 'parent_organization').all()

    def get_object(self):
        if self.request.user.role != 'admin':
            raise PermissionDenied("Admin access required.")
        return super().get_object()


class TeamView(APIView):
    """
    GET  /agents/team/             — developer sees their team members
    POST /agents/team/             — developer adds an existing agent to their team
    DELETE /agents/team/<agent_id>/ — developer removes agent from their team
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
        members = Agent.objects.filter(parent_organization=org).select_related('user')
        return Response(AgentSerializer(members, many=True).data)

    def post(self, request):
        org = self._get_org(request)
        agent_id = request.data.get('agent_id')
        if not agent_id:
            return Response({'detail': 'agent_id is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            agent = Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist:
            return Response({'detail': 'Agent not found.'}, status=status.HTTP_404_NOT_FOUND)

        if agent.id == org.id:
            return Response({'detail': 'Cannot add the organization itself as a member.'},
                            status=status.HTTP_400_BAD_REQUEST)

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
            return Response({'detail': 'Agent not found in your team.'},
                            status=status.HTTP_404_NOT_FOUND)

        agent.parent_organization = None
        agent.save(update_fields=['parent_organization', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)
