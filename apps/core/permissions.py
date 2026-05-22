from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdminUser(BasePermission):
    """Platform-level admin only."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'


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

    def has_object_permission(self, request, view, obj):
        if not request.user.is_authenticated:
            return False
        if request.user.role == 'admin':
            return True
        return getattr(obj, 'admin_user_id', None) == request.user.id


class IsOrgAdmin(BasePermission):
    """User is the admin of an organization (role=developer with owned_organization)."""
    def has_permission(self, request, view):
        if not request.user.is_authenticated or request.user.role != 'developer':
            return False
        return hasattr(request.user, 'owned_organization')

    def has_object_permission(self, request, view, obj):
        return request.user.is_authenticated and getattr(obj, 'admin_user_id', None) == request.user.id


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


# ── Helper ────────────────────────────────────────────────────────────────────

def get_org_membership(user, organization):
    """
    Return the active OrganizationMembership for (user, organization), or None.
    """
    from apps.organizations.models import OrganizationMembership
    try:
        return OrganizationMembership.objects.get(
            user=user, organization=organization, is_active=True
        )
    except OrganizationMembership.DoesNotExist:
        return None


def get_user_org(user):
    """
    Return the Organization for a non-admin user.
    Reads from OrganizationMembership when use_membership_rbac is enabled,
    otherwise falls back to the legacy User.organization FK approach.
    """
    from apps.config.services import SystemConfigService
    use_membership = SystemConfigService.get('use_membership_rbac', 'false') == 'true'

    if use_membership:
        from apps.organizations.models import OrganizationMembership
        membership = OrganizationMembership.objects.filter(
            user=user, is_active=True
        ).select_related('organization').first()
        return membership.organization if membership else None

    # Legacy path
    if user.role == 'developer':
        return getattr(user, 'owned_organization', None)
    if user.role == 'agent':
        try:
            return user.agent_profile.organization
        except Exception:
            return None
    return None


# ── Platform-level permissions ────────────────────────────────────────────────

class IsSuperAdmin(BasePermission):
    """Only the super_admin platform role."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == 'admin'
            and getattr(request.user, 'platform_role', None) == 'super_admin'
        )


class IsOpsAdmin(BasePermission):
    """super_admin or ops_admin."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == 'admin'
            and getattr(request.user, 'platform_role', None) in ('super_admin', 'ops_admin')
        )


class IsAIAdmin(BasePermission):
    """super_admin or ai_admin."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == 'admin'
            and getattr(request.user, 'platform_role', None) in ('super_admin', 'ai_admin')
        )


class IsComplianceAdmin(BasePermission):
    """super_admin or compliance_admin."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == 'admin'
            and getattr(request.user, 'platform_role', None) in ('super_admin', 'compliance_admin')
        )


class IsBillingAdmin(BasePermission):
    """super_admin or billing_admin."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == 'admin'
            and getattr(request.user, 'platform_role', None) in ('super_admin', 'billing_admin')
        )


class IsSupportAdmin(BasePermission):
    """Any admin role gets read-only org access for helpdesk."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'


# ── Org-level membership permissions ─────────────────────────────────────────
# These use has_object_permission(request, view, organization_instance).
# Platform admin always passes all org-level checks.

_ORG_ADMIN_ROLES     = ('owner', 'org_admin')
_TEAM_MANAGER_ROLES  = ('owner', 'org_admin', 'team_manager')
_SALES_MANAGER_ROLES = ('owner', 'org_admin', 'team_manager', 'sales_manager')
_CRM_OPERATOR_ROLES  = ('owner', 'org_admin', 'team_manager', 'sales_manager', 'crm_operator')
_AGENT_ROLES         = ('owner', 'org_admin', 'team_manager', 'sales_manager',
                        'crm_operator', 'agent', 'freelance_agent')
_ALL_ORG_ROLES       = ('owner', 'org_admin', 'team_manager', 'sales_manager',
                        'crm_operator', 'agent', 'freelance_agent', 'viewer')


def _membership_role(user, organization) -> str | None:
    m = get_org_membership(user, organization)
    return m.role if m else None


class IsOrgOwner(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated
    def has_object_permission(self, request, view, org):
        if request.user.role == 'admin':
            return True
        return _membership_role(request.user, org) == 'owner'


class IsOrgAdminMembership(BasePermission):
    """owner or org_admin. Named to avoid clash with existing IsOrgAdmin."""
    def has_permission(self, request, view):
        return request.user.is_authenticated
    def has_object_permission(self, request, view, org):
        if request.user.role == 'admin':
            return True
        return _membership_role(request.user, org) in _ORG_ADMIN_ROLES


class IsTeamManager(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated
    def has_object_permission(self, request, view, org):
        if request.user.role == 'admin':
            return True
        return _membership_role(request.user, org) in _TEAM_MANAGER_ROLES


class IsSalesManager(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated
    def has_object_permission(self, request, view, org):
        if request.user.role == 'admin':
            return True
        return _membership_role(request.user, org) in _SALES_MANAGER_ROLES


class IsCRMOperator(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated
    def has_object_permission(self, request, view, org):
        if request.user.role == 'admin':
            return True
        return _membership_role(request.user, org) in _CRM_OPERATOR_ROLES


class IsAgentOrAbove(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated
    def has_object_permission(self, request, view, org):
        if request.user.role == 'admin':
            return True
        return _membership_role(request.user, org) in _AGENT_ROLES


class IsViewerOrAbove(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated
    def has_object_permission(self, request, view, org):
        if request.user.role == 'admin':
            return True
        return _membership_role(request.user, org) in _ALL_ORG_ROLES