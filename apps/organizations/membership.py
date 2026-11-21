"""
Organization membership: single write path (ADR-013).

``OrganizationMembership`` is the canonical user ↔ organization link. Two legacy
columns are kept as *projections* that older readers still use:

* ``Organization.admin_user``  → the user's ``owner`` membership
* ``Agent.organization`` (+ ``Agent.user``) → the agent user's membership

Every change to a projection goes through :func:`sync_agent_membership` or
:func:`sync_owner_membership` (wired via model signals in ``signals.py``) so the
canonical table can no longer drift from the projections. Removing a member via
the membership API goes through :func:`remove_member`, which also clears the
``Agent.organization`` projection.

Owner memberships are never deactivated by an agent-side change: ownership is
transferred only by changing ``Organization.admin_user``.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

logger = logging.getLogger(__name__)

OWNER = 'owner'


def _model():
    from apps.organizations.models import OrganizationMembership
    return OrganizationMembership


def ensure_membership(user_id, organization_id, role, employment_type='internal', *,
                      promote=False):
    """
    Make sure ``user`` has an **active** membership in ``organization``.

    * missing → created with ``role``
    * inactive → reactivated with ``role``
    * active → kept as is, unless ``promote`` is set (used for ``owner``)

    Idempotent. Returns the membership.
    """
    Membership = _model()
    membership, created = Membership.objects.get_or_create(
        user_id=user_id,
        organization_id=organization_id,
        defaults={'role': role, 'employment_type': employment_type, 'is_active': True},
    )
    if created:
        logger.info('membership.created user=%s org=%s role=%s', user_id, organization_id, role)
        return membership

    if not membership.is_active:
        membership.is_active = True
        membership.left_at = None
        membership.role = role
        membership.employment_type = employment_type
        membership.save(update_fields=['is_active', 'left_at', 'role', 'employment_type'])
        logger.info('membership.reactivated user=%s org=%s role=%s', user_id, organization_id, role)
    elif promote and membership.role != role:
        membership.role = role
        membership.save(update_fields=['role'])
        logger.info('membership.promoted user=%s org=%s role=%s', user_id, organization_id, role)
    return membership


def deactivate_membership(user_id, organization_id, *, keep_owner=True) -> bool:
    """Deactivate the user's active membership in the org. Returns True if one changed."""
    Membership = _model()
    qs = Membership.objects.filter(
        user_id=user_id, organization_id=organization_id, is_active=True,
    )
    if keep_owner:
        qs = qs.exclude(role=OWNER)
    changed = qs.update(is_active=False, left_at=timezone.now())
    if changed:
        logger.info('membership.deactivated user=%s org=%s', user_id, organization_id)
    return bool(changed)


def sync_agent_membership(agent, previous_org_id, previous_user_id) -> None:
    """
    Mirror a change of ``Agent.organization`` / ``Agent.user`` into the canonical table.

    The agent's previous (user, org) membership is deactivated unless it is an owner
    membership; the new (user, org) membership is ensured.
    """
    new_org_id, new_user_id = agent.organization_id, agent.user_id
    if (previous_org_id, previous_user_id) == (new_org_id, new_user_id):
        return

    if previous_org_id and previous_user_id:
        deactivate_membership(previous_user_id, previous_org_id)

    if new_org_id and new_user_id:
        freelance = agent.employment_type == 'freelance'
        ensure_membership(
            new_user_id, new_org_id,
            role='freelance_agent' if freelance else 'agent',
            employment_type='freelance' if freelance else 'internal',
        )


def sync_owner_membership(organization, previous_admin_id) -> None:
    """Mirror a change of ``Organization.admin_user`` into the canonical table."""
    new_admin_id = organization.admin_user_id
    if previous_admin_id == new_admin_id:
        return
    if previous_admin_id:
        # The previous admin loses ownership, matching the legacy owned_organization path.
        deactivate_membership(previous_admin_id, organization.pk, keep_owner=False)
    if new_admin_id:
        ensure_membership(new_admin_id, organization.pk, role=OWNER, promote=True)


@transaction.atomic
def remove_member(membership) -> None:
    """
    Deactivate a membership and clear the agent projection when it points at this org.

    Owner memberships must be rejected by the caller (ownership is transferred via
    ``Organization.admin_user``).
    """
    from apps.agents.models import Agent

    membership.deactivate()
    agent = Agent.objects.filter(
        user_id=membership.user_id, organization_id=membership.organization_id,
    ).first()
    if agent is not None:
        agent.organization = None
        agent.employment_type = Agent.EmploymentType.FREELANCE
        agent.save(update_fields=['organization', 'employment_type', 'updated_at'])
        from apps.billing.ledger import UsageLedger
        UsageLedger.decrement_agents(str(membership.organization_id))


def primary_membership(user):
    """
    The user's primary active membership, chosen deterministically.

    Preference: the org the user owns, then the agent's ``Agent.organization``,
    then the earliest joined. Returns ``None`` when the user has no active membership.
    """
    Membership = _model()
    owned_id = _owned_org_id(user)
    agent_org_id = _agent_org_id(user)
    whens = []
    if owned_id:
        whens.append(When(organization_id=owned_id, then=Value(0)))
    if agent_org_id:
        whens.append(When(organization_id=agent_org_id, then=Value(1)))
    qs = Membership.objects.filter(user=user, is_active=True).select_related('organization')
    if whens:
        qs = qs.annotate(_pref=Case(*whens, default=Value(2), output_field=IntegerField()))
        return qs.order_by('_pref', 'joined_at', 'pk').first()
    return qs.order_by('joined_at', 'pk').first()


def _owned_org_id(user):
    from apps.organizations.models import Organization
    try:
        return user.owned_organization.pk
    except (Organization.DoesNotExist, AttributeError):
        return None


def _agent_org_id(user):
    from apps.agents.models import Agent
    try:
        return user.agent_profile.organization_id
    except (Agent.DoesNotExist, AttributeError):
        return None


# ── Drift detection / repair (manage.py reconcile_memberships) ────────────────

REPAIRABLE = ('missing_owner', 'missing_agent')
REPORT_ONLY = ('extra_owner', 'orphan_agent', 'collaboration_without_membership',
               'deprecated_agent_organization_memberships')


def find_drift() -> dict[str, list]:
    """
    Compare the canonical table with its projections.

    Repairable (by ``repair_drift``):
      missing_owner  — (user, org) where org.admin_user has no active ``owner`` membership
      missing_agent  — (user, org) where Agent.user + Agent.organization has no active membership
    Report only (need a human decision, see ADR-013):
      extra_owner    — active owner memberships whose user is not org.admin_user
      orphan_agent   — active agent/freelance_agent memberships not backed by
                       Agent.organization or an active AgentOrgMembership
      collaboration_without_membership — active AgentOrgMembership whose agent user
                       has no active membership in that org
      deprecated_agent_organization_memberships — rows in the unused
                       AgentOrganizationMembership table
    """
    from apps.agents.models import Agent, AgentOrganizationMembership, AgentOrgMembership
    from apps.organizations.models import Organization

    Membership = _model()
    active = {
        (u, o): r for u, o, r in
        Membership.objects.filter(is_active=True).values_list('user_id', 'organization_id', 'role')
    }
    admins = dict(Organization.objects.filter(admin_user__isnull=False)
                  .values_list('id', 'admin_user_id'))
    agent_links = set(Agent.objects.filter(user__isnull=False, organization__isnull=False)
                      .values_list('user_id', 'organization_id'))
    collab_links = set(AgentOrgMembership.objects.filter(status='active', agent__user__isnull=False)
                       .values_list('agent__user_id', 'organization_id'))

    return {
        'missing_owner': sorted(
            (str(u), str(o)) for o, u in admins.items() if active.get((u, o)) != OWNER),
        'missing_agent': sorted(
            (str(u), str(o)) for u, o in agent_links if (u, o) not in active),
        'extra_owner': sorted(
            (str(u), str(o)) for (u, o), r in active.items()
            if r == OWNER and admins.get(o) != u),
        'orphan_agent': sorted(
            (str(u), str(o)) for (u, o), r in active.items()
            if r in ('agent', 'freelance_agent')
            and (u, o) not in agent_links and (u, o) not in collab_links),
        'collaboration_without_membership': sorted(
            (str(u), str(o)) for u, o in collab_links if (u, o) not in active),
        'deprecated_agent_organization_memberships': sorted(
            (str(f), str(o)) for f, o in AgentOrganizationMembership.objects
            .values_list('freelance_agent__user_id', 'organization_id')),
    }


@transaction.atomic
def repair_drift(drift: dict[str, list] | None = None) -> dict[str, int]:
    """Fix the repairable categories of ``find_drift``. Returns counts fixed."""
    from apps.agents.models import Agent

    drift = drift if drift is not None else find_drift()
    for user_id, org_id in drift['missing_owner']:
        ensure_membership(user_id, org_id, role=OWNER, promote=True)
    for user_id, org_id in drift['missing_agent']:
        agent = Agent.objects.get(user_id=user_id, organization_id=org_id)
        sync_agent_membership(agent, previous_org_id=None, previous_user_id=None)
    return {k: len(drift[k]) for k in REPAIRABLE}
