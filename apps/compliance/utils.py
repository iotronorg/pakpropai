_GDPR_REGIONS = frozenset({'eu', 'uk'})


def org_is_gdpr_jurisdiction(org) -> bool:
    """Return True if the org's data_residency_region requires GDPR compliance."""
    return getattr(org, 'data_residency_region', 'global') in _GDPR_REGIONS


def get_request_org(request):
    """Return the organization for the authenticated request user, or None."""
    user = request.user
    if user.role == 'admin':
        return None
    if user.role == 'developer':
        return getattr(user, 'owned_organization', None)
    if user.role == 'agent':
        try:
            return user.agent_profile.organization
        except Exception:
            return None
    return None
