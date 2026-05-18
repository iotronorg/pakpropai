from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdminUser(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'


class IsOrgAdmin(BasePermission):
    """The request user is the admin_user of the target organization."""
    def has_object_permission(self, request, view, obj):
        return request.user.is_authenticated and obj.admin_user_id == request.user.id


class IsAdminOrOrgAdmin(BasePermission):
    """Platform admin, or the org's own admin_user."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('admin', 'developer')

    def has_object_permission(self, request, view, obj):
        if request.user.role == 'admin':
            return True
        if request.method in SAFE_METHODS:
            return obj.admin_user_id == request.user.id
        return obj.admin_user_id == request.user.id
