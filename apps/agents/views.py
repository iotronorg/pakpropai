from rest_framework import generics, permissions
from rest_framework.exceptions import NotFound
from .models import Agent
from .serializers import AgentSerializer


class AgentMeView(generics.RetrieveUpdateAPIView):
    """GET /agents/me/ — returns the agent profile for the logged-in user."""
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
