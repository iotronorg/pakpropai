_GDPR_REGIONS = frozenset({'eu', 'uk'})


def org_is_gdpr_jurisdiction(org) -> bool:
    """Return True if the org's data_residency_region requires GDPR compliance."""
    return getattr(org, 'data_residency_region', 'global') in _GDPR_REGIONS


def get_request_org(request):
    """Return the organization for the authenticated request user, or None."""
    user = request.user
    if user.role == 'admin':
        return None
    from apps.core.permissions import get_admin_org, get_user_org
    if user.role == 'developer':
        return get_admin_org(user)
    if user.role == 'agent':
        return get_user_org(user)
    return None
