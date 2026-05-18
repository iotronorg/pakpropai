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


class IsAdminOrOrgAdmin(BasePermission):
    """Platform admin or organization admin (role=developer)."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in ('admin', 'developer')
        )


class IsOrgAdmin(BasePermission):
    """User is the admin of an organization (role=developer with owned_organization)."""
    def has_permission(self, request, view):
        if not request.user.is_authenticated or request.user.role != 'developer':
            return False
        return hasattr(request.user, 'owned_organization')


class IsOrgMember(BasePermission):
    """
    User belongs to an organization — either as its admin (developer) or
    as an agent whose agent_profile.organization is set.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.role == 'developer':
            return hasattr(request.user, 'owned_organization')
        if request.user.role == 'agent':
            try:
                return request.user.agent_profile.organization_id is not None
            except Exception:
                return False
        return False


# Backward-compat alias — prefer IsAdminOrOrgAdmin in new code.
IsAdminOrDeveloper = IsAdminOrOrgAdmin