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


class AgentListView(generics.ListAPIView):
    """GET /agents/ — admin-only list of all agents."""
    serializer_class   = AgentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.role != 'admin':
            return Agent.objects.none()
        return Agent.objects.select_related('user', 'parent_organization').all()


class AgentAdminSerializer(AgentSerializer):
    """Extends AgentSerializer to allow admins to write is_verified/is_active/is_featured."""
    class Meta(AgentSerializer.Meta):
        read_only_fields = (
            'id', 'total_leads', 'total_listings',
            'closed_deals', 'user_phone', 'user_email',
            'parent_organization_name', 'joined_at', 'updated_at',
        )


class AgentAdminDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /agents/{id}/ — admin only; can toggle is_verified, is_active, is_featured."""
    serializer_class   = AgentAdminSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset           = Agent.objects.select_related('user', 'parent_organization').all()

    def get_object(self):
        if self.request.user.role != 'admin':
            raise PermissionDenied("Admin access required.")
        return super().get_object()
