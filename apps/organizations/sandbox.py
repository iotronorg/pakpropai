"""
Single source of truth for the meaning of ``is_sandbox`` (see ADR-002 in
docs/development/ARCHITECTURE_DECISIONS.md).

A record is *sandbox* when it belongs to an organization that has not reached
``operational_mode == 'production'``. Records without an organization are
decided by the caller (e.g. platform demo listings are sandbox, private-client
listings are not); for leads and WhatsApp sessions see :func:`contact_is_sandbox`.
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


_UNLOADED = object()


def contact_is_sandbox(organization_id) -> bool:
    """
    Leads and WhatsApp sessions (T-1040, KI-017): the flag follows the
    organization. A record with **no** organization is a real person on the
    platform's shared number, so it is live (``False``): there is no
    platform-level sandbox.
    """
    return org_is_sandbox(organization_id) if organization_id else False


class SandboxFollowsOrg:
    """
    Model mixin: derive ``is_sandbox`` from ``organization`` on insert and
    whenever the organization changes (``update_fields``-aware). A change of the
    org's own ``operational_mode`` is the provisioner's job (ADR-002), not an
    incidental save.
    """

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._sandbox_org_at_load = instance.__dict__.get('organization_id', _UNLOADED)
        return instance

    def _derive_sandbox(self, kwargs) -> None:
        update_fields = kwargs.get('update_fields')
        loaded = getattr(self, '_sandbox_org_at_load', _UNLOADED)
        if self._state.adding:
            changed = True
        elif loaded is _UNLOADED:
            return
        else:
            changed = loaded != self.organization_id
        if not changed:
            return
        if update_fields is not None and not ({'organization', 'organization_id'} & set(update_fields)):
            return
        self.is_sandbox = contact_is_sandbox(self.organization_id)
        if update_fields is not None:
            kwargs['update_fields'] = {*update_fields, 'is_sandbox'}

    def save(self, *args, **kwargs):
        self._derive_sandbox(kwargs)
        super().save(*args, **kwargs)
        self._sandbox_org_at_load = self.organization_id
