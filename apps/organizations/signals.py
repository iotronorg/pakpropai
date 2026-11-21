from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings


@receiver(post_save, sender='organizations.OrganizationTheme')
def invalidate_theme_cache(sender, instance, **kwargs):
    platform_domain = getattr(settings, 'REALTRON_PLATFORM_DOMAIN', 'realtron.ai')
    org = instance.organization
    keys = [f'theme_cfg:{org.slug}.{platform_domain}']
    if org.custom_domain:
        keys.append(f'theme_cfg:{org.custom_domain}')
    cache.delete_many(keys)


# ── Membership projections (ADR-013) ──────────────────────────────────────────
# Agent.organization/Agent.user and Organization.admin_user are projections of the
# canonical OrganizationMembership table. Snapshot them when an instance is loaded
# and mirror any change on save. Reads use __dict__ so deferred fields never
# trigger a query; an unknown previous value only ensures the new membership.

from django.db.models.signals import post_init  # noqa: E402

_UNKNOWN = object()


def _snap(instance, attrs):
    return tuple(instance.__dict__.get(a, _UNKNOWN) for a in attrs)


_AGENT_ATTRS = ('organization_id', 'user_id')
_AGENT_FIELDS = {'organization', 'organization_id', 'user', 'user_id', 'employment_type'}
_ORG_ATTRS = ('admin_user_id',)
_ORG_FIELDS = {'admin_user', 'admin_user_id'}


@receiver(post_init, sender='agents.Agent')
def _agent_snapshot(sender, instance, **kwargs):
    instance._membership_snapshot = _snap(instance, _AGENT_ATTRS)


@receiver(post_init, sender='organizations.Organization')
def _org_snapshot(sender, instance, **kwargs):
    instance._membership_snapshot = _snap(instance, _ORG_ATTRS)


def _skip(kwargs, watched) -> bool:
    if kwargs.get('raw'):
        return True
    update_fields = kwargs.get('update_fields')
    return update_fields is not None and not (set(update_fields) & watched)


@receiver(post_save, sender='agents.Agent')
def sync_agent_membership_on_save(sender, instance, created, **kwargs):
    from apps.organizations.membership import sync_agent_membership
    if not _skip(kwargs, _AGENT_FIELDS):
        prev_org, prev_user = (None, None) if created else getattr(
            instance, '_membership_snapshot', (_UNKNOWN, _UNKNOWN))
        sync_agent_membership(
            instance,
            previous_org_id=None if prev_org is _UNKNOWN else prev_org,
            previous_user_id=None if prev_user is _UNKNOWN else prev_user,
        )
    instance._membership_snapshot = _snap(instance, _AGENT_ATTRS)


@receiver(post_save, sender='organizations.Organization')
def sync_owner_membership_on_save(sender, instance, created, **kwargs):
    from apps.organizations.membership import sync_owner_membership
    if not _skip(kwargs, _ORG_FIELDS):
        (prev_admin,) = (None,) if created else getattr(instance, '_membership_snapshot', (_UNKNOWN,))
        sync_owner_membership(instance, previous_admin_id=None if prev_admin is _UNKNOWN else prev_admin)
    instance._membership_snapshot = _snap(instance, _ORG_ATTRS)
