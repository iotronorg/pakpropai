from rest_framework import viewsets, permissions
from .models import Lead
from .serializers import LeadSerializer


class IsDashboardUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('admin', 'agent', 'developer')


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class   = LeadSerializer
    permission_classes = [IsDashboardUser]
    http_method_names  = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = Lead.objects.select_related('user', 'assigned_agent')
        role = self.request.user.role
        if role == 'agent':
            # Agents only see leads assigned to their agent profile
            try:
                agent = self.request.user.agent_profile
                return qs.filter(assigned_agent=agent)
            except Exception:
                return qs.none()
        # admin and developer see all leads
        return qs.all()
