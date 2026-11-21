"""
Single source of truth for the meaning of ``is_sandbox`` (see ADR-002 in
docs/development/ARCHITECTURE_DECISIONS.md).

A record is *sandbox* when it belongs to an organization that has not reached
``operational_mode == 'production'``. Records without an organization are
decided by the caller (e.g. platform demo listings are sandbox, private-client
listings are not).
"""

PRODUCTION_MODE = 'production'


def org_is_sandbox(org_or_id) -> bool:
    """
    True when the organization is not in production mode.

    Accepts an Organization instance or a primary key. Unknown / missing
    organizations are treated as sandbox (fail closed for visibility).
    """
    if org_or_id is None:
        return True
    mode = getattr(org_or_id, 'operational_mode', None)
    if mode is None:
        from apps.organizations.models import Organization
        mode = (
            Organization.objects
            .filter(pk=org_or_id)
            .values_list('operational_mode', flat=True)
            .first()
        )
    return mode != PRODUCTION_MODE
