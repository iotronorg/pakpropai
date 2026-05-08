from rest_framework import generics, permissions, serializers as drf_serializers
from rest_framework.exceptions import NotFound, PermissionDenied
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
    """GET /agents/ — admin list of all agents. POST /agents/ — admin creates agent."""
    serializer_class   = AgentAdminSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.role != 'admin':
            return Agent.objects.none()
        return Agent.objects.select_related('user', 'parent_organization').all()

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
