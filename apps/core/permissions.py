import logging
from contextvars import ContextVar

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS

logger = logging.getLogger(__name__)
_last_foreign_selector: ContextVar[tuple | None] = ContextVar('last_foreign_selector', default=None)


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


def _object_org_id(obj):
    """Organization id an object belongs to: the org itself, or obj.organization_id."""
    from apps.organizations.models import Organization
    if isinstance(obj, Organization):
        return obj.pk
    return getattr(obj, 'organization_id', None)


class IsAdminOrOrgAdmin(BasePermission):
    """
    Platform admin, or ``owner``/``org_admin`` of the active organization
    (ADR-020; membership-based — ``user.role`` no longer decides).
    Object check: the object's organization must be one the user administers.
    """
    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if user.role == 'admin':
            return True
        return active_org_role(user) in _ORG_ADMIN_ROLES

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user.is_authenticated:
            return False
        if user.role == 'admin':
            return True
        org_id = _object_org_id(obj)
        return org_id is not None and _membership_role_by_id(user, org_id) in _ORG_ADMIN_ROLES


class IsOrgAdmin(BasePermission):
    """``owner``/``org_admin`` of the active organization (platform admins excluded)."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and active_org_role(request.user) in _ORG_ADMIN_ROLES

    def has_object_permission(self, request, view, obj):
        org_id = _object_org_id(obj)
        return (request.user.is_authenticated and org_id is not None
                and _membership_role_by_id(request.user, org_id) in _ORG_ADMIN_ROLES)


class IsOrgMember(BasePermission):
    """Any active membership in the active organization."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and active_org_role(request.user) in _ALL_ORG_ROLES


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


def get_active_membership(user):
    """
    The membership that defines the user's organization for this request
    (ADR-020): the one named by the ``X-Organization-ID`` header when present,
    otherwise the primary membership. A header naming an org the user is not
    an active member of yields ``None`` (no access), never the primary org.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    from apps.core.context import INVALID_ORG, get_requested_org_id
    from apps.organizations.models import OrganizationMembership

    requested = get_requested_org_id()
    if requested == INVALID_ORG:
        return None
    if requested:
        membership = (OrganizationMembership.objects
                      .filter(user=user, organization_id=requested, is_active=True)
                      .select_related('organization').first())
        if membership is None:
            _record_foreign_org_selector(user, requested)
        return membership
    from apps.organizations.membership import primary_membership
    return primary_membership(user)


def _record_foreign_org_selector(user, org_id):
    """Security event for X-Organization-ID naming an org the user is not in (once per request)."""
    from apps.core.context import get_request_id
    key = (str(user.pk), org_id, get_request_id())
    if _last_foreign_selector.get() == key:
        return
    _last_foreign_selector.set(key)
    try:
        from apps.security.models import ApiSecurityEvent
        ApiSecurityEvent.objects.create(
            event_type='cross_tenant', severity=4, user_id=str(user.pk), organization_id='',
            endpoint='', http_method='',
            threat_detail=f'X-Organization-ID {org_id} is not an active membership of the user',
            request_id=get_request_id()[:64],
        )
    except Exception:  # never turn a denial into a 500
        logger.exception('permissions: could not record cross-tenant selector')


def get_user_org(user):
    """
    The Organization a non-admin user is acting for: the active membership's
    org (ADR-020). Membership is the only source; the legacy projections
    (``owned_organization``, ``agent_profile.organization``) are kept in sync by
    ADR-013 phase A but are no longer read here.
    """
    membership = get_active_membership(user)
    return membership.organization if membership else None


def active_org_role(user) -> str | None:
    membership = get_active_membership(user)
    return membership.role if membership else None


def get_admin_org(user):
    """
    The active organization if the user administers it (``owner``/``org_admin``),
    else ``None``. Use for org‑management actions (billing, API keys,
    provisioning, team management) that the legacy code tied to
    ``owned_organization``: the selector can never raise a user's privileges.
    """
    membership = get_active_membership(user)
    if membership is not None and membership.role in _ORG_ADMIN_ROLES:
        return membership.organization
    return None


def primary_org(user):
    """Primary organization of a *subject* user (tasks, audit records) — ignores the request selector."""
    if user is None:
        return None
    try:
        from apps.organizations.membership import primary_membership
        membership = primary_membership(user)
    except Exception:
        return None
    return membership.organization if membership else None


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


def _membership_role_by_id(user, organization_id) -> str | None:
    from apps.organizations.models import OrganizationMembership
    return (OrganizationMembership.objects
            .filter(user=user, organization_id=organization_id, is_active=True)
            .values_list('role', flat=True).first())


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

class FeatureFlagDisabled(PermissionDenied):
    default_code = 'feature_disabled'
    default_detail = 'This feature is not available.'


def FeatureEnabled(flag: str, methods=None):
    """
    Permission factory: deny with 403 ``feature_disabled`` while SystemConfig
    ``flag`` is off. ``methods`` limits the gate to those HTTP methods (e.g. only
    creation), so existing records stay readable/manageable when a module is frozen.
    """
    gated = {m.upper() for m in methods} if methods else None

    class _FeatureEnabled(BasePermission):
        def has_permission(self, request, view):
            if gated is not None and request.method.upper() not in gated:
                return True
            from apps.config.services import SystemConfigService
            if SystemConfigService.get_features().get(flag, False):
                return True
            raise FeatureFlagDisabled()

    _FeatureEnabled.__name__ = f'FeatureEnabled[{flag}]'
    return _FeatureEnabled
