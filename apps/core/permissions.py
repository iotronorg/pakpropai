# apps/core/permissions.py
from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsOwnerOrReadOnly(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if getattr(request.user, 'role', None) == 'admin':
            return True
        return getattr(obj, 'owner_id', None) == request.user.id


class IsAgentOrAdmin(BasePermission):
    """Blocks role=user clients from write operations (agent/developer/admin only)."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in ('agent', 'admin', 'developer')
        )


class IsAdminOrDeveloper(BasePermission):
    """Restricts to admin or developer org roles; blocks agent and client roles."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in ('admin', 'developer')
        )