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
        return Lead.objects.select_related('user').all()
