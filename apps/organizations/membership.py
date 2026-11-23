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

Owner memberships are never deactivated by an agent-side change. Ownership is
transferred through :func:`transfer_ownership` (ADR-021), which moves the owner
membership and the ``admin_user`` projection together.

Phase C, release 1 (T-1032b, ADR-013 addendum): the **owner membership is the
source of truth** for who owns an org. Readers use :func:`owner_of` /
:func:`owned_organization_ids` (``Organization.owner``), never ``admin_user``.
``admin_user`` is now a best-effort projection for older builds, written only by
:func:`sync_admin_projection`: the owner, or NULL when that user already holds
the one-to-one ``admin_user`` of another org (one user may own several orgs).
The column is dropped by the staged contract migration
(``apps/organizations/contract_migrations``).

Member management (invite, role change, removal, transfer) is the membership
contract below: the views only parse the request and call it.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

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


def owner_of(organization):
    """
    The user who owns ``organization`` (active ``owner`` membership; the earliest
    if drift left several). Falls back to the legacy ``admin_user`` projection
    only when no owner membership exists. ``None`` for an ownerless org.
    """
    if organization is None:
        return None
    m = (_model().objects.filter(organization_id=organization.pk, role=OWNER, is_active=True)
         .select_related('user').order_by('joined_at', 'pk').first())
    if m is not None:
        return m.user
    return organization.admin_user if organization.admin_user_id else None


def owned_organization_ids(user) -> list:
    """Ids of every org the user owns (active owner memberships), oldest first."""
    if user is None or not getattr(user, 'pk', None):
        return []
    return list(_model().objects.filter(user_id=user.pk, role=OWNER, is_active=True)
                .order_by('joined_at', 'pk').values_list('organization_id', flat=True))


def sync_admin_projection(organization_id) -> None:
    """
    Point the legacy ``Organization.admin_user`` at the owner membership.

    ``admin_user`` is one-to-one, so a user who already projects into another org
    leaves this org's column NULL (readers no longer depend on it). Written with
    ``QuerySet.update`` so the legacy post_save hook does not run.
    """
    from apps.organizations.models import Organization
    owner_id = (_model().objects.filter(organization_id=organization_id, role=OWNER, is_active=True)
                .order_by('joined_at', 'pk').values_list('user_id', flat=True).first())
    if owner_id and Organization.objects.filter(admin_user_id=owner_id).exclude(pk=organization_id).exists():
        owner_id = None
    Organization.objects.filter(pk=organization_id).exclude(admin_user_id=owner_id).update(
        admin_user_id=owner_id)


@transaction.atomic
def set_owner(organization, user) -> None:
    """
    Make ``user`` the sole owner of ``organization`` (org registration, platform
    admin create/PATCH). Previous owners lose their membership, matching the
    legacy ``admin_user`` semantics; member-initiated hand-over is
    :func:`transfer_ownership`. ``user=None`` leaves the org ownerless.
    """
    Membership = _model()
    previous = list(Membership.objects.select_for_update()
                    .filter(organization_id=organization.pk, role=OWNER, is_active=True)
                    .exclude(user_id=getattr(user, 'pk', None)).values_list('user_id', flat=True))
    for uid in previous:
        deactivate_membership(uid, organization.pk, keep_owner=False)
    if user is not None:
        ensure_membership(user.pk, organization.pk, role=OWNER, promote=True)
    sync_admin_projection(organization.pk)
    for uid in previous:                      # their admin_user slot may now be free
        for oid in (Membership.objects.filter(user_id=uid, role=OWNER, is_active=True)
                    .values_list('organization_id', flat=True)):
            sync_admin_projection(oid)
    projected = type(organization).objects.values_list('admin_user_id', flat=True).get(pk=organization.pk)
    organization.admin_user_id = projected
    organization._membership_snapshot = (projected,)


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


# ── Membership contract (T-1032a, ADR-021) ────────────────────────────────────
#
# Invite, role change, removal and ownership transfer are decided here, not in
# views: one grant policy (ADR-020), one audit trail (``AuditLog``), one place
# that keeps the ``Organization.admin_user`` projection in step with the owner
# membership. Every operation is atomic together with its audit row.

ORG_ADMIN = 'org_admin'
PLATFORM_ADMIN = 'platform_admin'
_MANAGER_ROLES = (OWNER, ORG_ADMIN, PLATFORM_ADMIN)
# Granting, changing or removing these roles is reserved to the owner (or a platform admin).
OWNER_ONLY_ROLES = (ORG_ADMIN,)
# The account type the rest of the code scopes as an organization operator.
OWNER_ACCOUNT_TYPE = 'developer'


class MembershipInvalid(APIException):
    status_code = 400
    default_detail = 'Invalid membership request.'
    default_code = 'membership_invalid'


class MembershipConflict(APIException):
    status_code = 409
    default_detail = 'The membership is in a conflicting state.'
    default_code = 'membership_conflict'


class MembershipForbidden(PermissionDenied):
    default_code = 'membership_forbidden'


def actor_role(actor, organization_id) -> str | None:
    """``platform_admin`` for platform admins, else the actor's active role in the org."""
    if actor is None:
        return None
    if getattr(actor, 'role', None) == 'admin':
        return PLATFORM_ADMIN
    return (_model().objects
            .filter(user_id=actor.pk, organization_id=organization_id, is_active=True)
            .values_list('role', flat=True).first())


def _require_manager(role):
    if role not in _MANAGER_ROLES:
        raise MembershipForbidden('Only organization admins can manage members.')


def _require_owner_for(role, actor_role_, message):
    if role in OWNER_ONLY_ROLES and actor_role_ not in (OWNER, PLATFORM_ADMIN):
        raise MembershipForbidden(message)


def _validate_choices(role, employment_type=None):
    Membership = _model()
    if role not in Membership.Role.values:
        raise MembershipInvalid(f'Invalid role: {role}')
    if employment_type is not None and employment_type not in Membership.EmploymentType.values:
        raise MembershipInvalid(f'Invalid employment_type: {employment_type}')


def _snapshot(m) -> dict:
    return {
        'user_id': str(m.user_id), 'organization_id': str(m.organization_id),
        'role': m.role, 'employment_type': m.employment_type, 'is_active': m.is_active,
    }


def _audit(actor, action, target, detail, *, before=None, after=None, request=None):
    from apps.core.models import AuditLog
    if actor is not None and not getattr(actor, 'is_authenticated', False):
        actor = None
    AuditLog.log(actor=actor, action=action, target=target, detail=detail,
                 before=before, after=after, request=request)


def check_invite(actor, organization, role, employment_type) -> None:
    """Validate an invite before the invitee is looked up (no user enumeration past a denial)."""
    _validate_choices(role, employment_type)
    caller = actor_role(actor, organization.pk)
    _require_manager(caller)
    if role == OWNER:
        raise MembershipForbidden(
            'The owner role cannot be granted by invitation; use ownership transfer.')
    _require_owner_for(role, caller, 'Only the organization owner can grant this role.')


@transaction.atomic
def invite_member(actor, organization, user, role, employment_type='internal', *, request=None):
    """
    Add ``user`` to ``organization`` with ``role`` (or reactivate a former membership).

    Raises ``MembershipConflict`` when the user is already an active member.
    Returns ``(membership, created)``.
    """
    from apps.core.models import AuditLog

    check_invite(actor, organization, role, employment_type)
    Membership = _model()
    membership = (Membership.objects.select_for_update()
                  .filter(user=user, organization=organization).first())
    if membership is not None and membership.is_active:
        raise MembershipConflict('User is already a member of this organization.')

    before = _snapshot(membership) if membership is not None else None
    if membership is None:
        try:
            with transaction.atomic():  # savepoint: a concurrent invite wins the unique key
                membership = Membership.objects.create(
                    user=user, organization=organization, role=role,
                    employment_type=employment_type, is_active=True,
                )
        except IntegrityError:
            raise MembershipConflict('User is already a member of this organization.')
        created, detail = True, 'membership.invited'
    else:
        membership.is_active = True
        membership.left_at = None
        membership.role = role
        membership.employment_type = employment_type
        membership.save(update_fields=['is_active', 'left_at', 'role', 'employment_type'])
        created, detail = False, 'membership.reactivated'

    _audit(actor, AuditLog.Action.CREATE, membership, detail,
           before=before, after=_snapshot(membership), request=request)
    logger.info('%s user=%s org=%s role=%s by=%s', detail, user.pk, organization.pk, role,
                getattr(actor, 'pk', None))
    return membership, created


# ── Invitations (T-1032c, KI-041): pending until the invitee accepts ────────

def _invite_snapshot(inv) -> dict:
    return {'invitee_id': str(inv.invitee_id), 'organization_id': str(inv.organization_id),
            'role': inv.role, 'employment_type': inv.employment_type, 'status': inv.status}


def _notify_invitee(invite) -> None:
    """WhatsApp notice through the outbox, in the caller's transaction (one per invite)."""
    from apps.markets.services import format_local
    from apps.notifications.delivery import enqueue_whatsapp_text
    org = invite.organization
    role = invite.get_role_display()
    expires = format_local(invite.expires_at, org, '%d %b %Y')
    enqueue_whatsapp_text(
        invite.invitee.phone,
        f"{org.name} has invited you to join their team as {role}. "
        f"Open the app to accept or decline. The invitation expires on {expires}.",
        key=f'org_invite:{invite.pk}', organization=org,
    )


@transaction.atomic
def create_invite(actor, organization, user, role, employment_type='internal', *, request=None):
    """
    Invite ``user`` to ``organization``. Nothing is granted until they accept.

    Raises ``MembershipConflict`` when the user is already an active member or
    already has an open invite. Returns the ``OrganizationInvite``.
    """
    from datetime import timedelta
    from django.conf import settings
    from apps.core.models import AuditLog
    from apps.organizations.models import OrganizationInvite as Invite

    check_invite(actor, organization, role, employment_type)
    if _model().objects.filter(user=user, organization=organization, is_active=True).exists():
        raise MembershipConflict('User is already a member of this organization.')
    now = timezone.now()
    # A lapsed invite no longer blocks a new one.
    (Invite.objects.filter(organization=organization, invitee=user,
                           status=Invite.Status.PENDING, expires_at__lte=now)
     .update(status=Invite.Status.EXPIRED, responded_at=now))
    try:
        with transaction.atomic():
            invite = Invite.objects.create(
                organization=organization, invitee=user, invited_by=actor, role=role,
                employment_type=employment_type,
                expires_at=now + timedelta(days=getattr(settings, 'ORG_INVITE_TTL_DAYS', 7)),
            )
    except IntegrityError:
        raise MembershipConflict('User already has a pending invitation to this organization.')
    _audit(actor, AuditLog.Action.CREATE, invite, 'membership.invite_created',
           after=_invite_snapshot(invite), request=request)
    _notify_invitee(invite)
    logger.info('membership.invite_created invite=%s user=%s org=%s role=%s by=%s',
                invite.pk, user.pk, organization.pk, role, getattr(actor, 'pk', None))
    return invite


def _lock_open_invite(invite_id, **filters):
    from apps.organizations.models import OrganizationInvite as Invite
    invite = (Invite.objects.select_for_update().select_related('organization', 'invitee')
              .filter(pk=invite_id, **filters).first())
    if invite is None:
        raise NotFound('Invitation not found.')
    if invite.status == Invite.Status.PENDING and not invite.is_open:
        invite.status, invite.responded_at = Invite.Status.EXPIRED, timezone.now()
        invite.save(update_fields=['status', 'responded_at'])
    return invite                                    # callers refuse a non-pending invite


def _closed(invite):
    """Raised by callers *after* their transaction, so an expiry mark is kept."""
    return MembershipConflict(f'This invitation is {invite.status}.')


def respond_to_invite(user, invite_id, *, accept: bool, request=None):
    """
    The invitee accepts or declines. Accepting re-checks the grant policy with
    the inviter's *current* rights (an inviter who lost them voids the invite),
    then creates or reactivates the membership. Returns ``(invite, membership|None)``;
    a voided invite comes back with status ``revoked`` and no membership.
    """
    from apps.core.models import AuditLog
    from apps.organizations.models import OrganizationInvite as Invite

    with transaction.atomic():
        invite = _lock_open_invite(invite_id, invitee=user)
        if invite.status != Invite.Status.PENDING:
            closed = invite
        else:
            closed = None
            membership = _respond(user, invite, accept, request)
    if closed is not None:
        raise _closed(closed)
    return invite, membership


def _respond(user, invite, accept, request):
    from apps.core.models import AuditLog
    from apps.organizations.models import OrganizationInvite as Invite
    before = _invite_snapshot(invite)
    membership = None
    if accept:
        try:
            with transaction.atomic():               # savepoint: keep the REVOKED update below
                membership, _ = invite_member(invite.invited_by, invite.organization, user,
                                              invite.role, invite.employment_type, request=request)
            invite.status, detail = Invite.Status.ACCEPTED, 'membership.invite_accepted'
        except MembershipForbidden:
            invite.status, detail = Invite.Status.REVOKED, 'membership.invite_voided'
    else:
        invite.status, detail = Invite.Status.DECLINED, 'membership.invite_declined'
    invite.responded_at = timezone.now()
    invite.save(update_fields=['status', 'responded_at'])
    _audit(user, AuditLog.Action.UPDATE, invite, detail,
           before=before, after=_invite_snapshot(invite), request=request)
    return membership


def revoke_invite(actor, organization, invite_id, *, request=None):
    """An org manager withdraws an open invitation."""
    from apps.core.models import AuditLog
    from apps.organizations.models import OrganizationInvite as Invite

    _require_manager(actor_role(actor, organization.pk))
    with transaction.atomic():
        invite = _lock_open_invite(invite_id, organization=organization)
        if invite.status == Invite.Status.PENDING:
            before = _invite_snapshot(invite)
            invite.status, invite.responded_at = Invite.Status.REVOKED, timezone.now()
            invite.save(update_fields=['status', 'responded_at'])
            _audit(actor, AuditLog.Action.UPDATE, invite, 'membership.invite_revoked',
                   before=before, after=_invite_snapshot(invite), request=request)
            return invite
    raise _closed(invite)


@transaction.atomic
def change_role(actor, membership, role, employment_type=None, *, request=None):
    """
    Change an active member's role (and optionally employment type).

    * the owner's membership cannot be changed here (transfer ownership first);
    * ``owner`` cannot be granted here;
    * granting or revoking ``org_admin`` is owner‑only.
    """
    from apps.core.models import AuditLog

    _validate_choices(role, employment_type)
    Membership = _model()
    membership = Membership.objects.select_for_update().get(pk=membership.pk)
    if not membership.is_active:
        raise MembershipConflict('The membership is not active.')
    caller = actor_role(actor, membership.organization_id)
    _require_manager(caller)
    if membership.role == OWNER:
        raise MembershipForbidden(
            "The owner's role cannot be changed; transfer ownership first.")
    if role == OWNER:
        raise MembershipForbidden(
            'The owner role cannot be granted by a role change; use ownership transfer.')
    _require_owner_for(role, caller, 'Only the organization owner can grant this role.')
    _require_owner_for(membership.role, caller,
                       'Only the organization owner can change an org admin.')

    new_employment = employment_type or membership.employment_type
    if (role, new_employment) == (membership.role, membership.employment_type):
        return membership

    before = _snapshot(membership)
    membership.role = role
    membership.employment_type = new_employment
    membership.save(update_fields=['role', 'employment_type'])
    _audit(actor, AuditLog.Action.UPDATE, membership, 'membership.role_changed',
           before=before, after=_snapshot(membership), request=request)
    logger.info('membership.role_changed id=%s %s→%s by=%s', membership.pk,
                before['role'], role, getattr(actor, 'pk', None))
    return membership


@transaction.atomic
def transfer_ownership(actor, organization, membership_id, *, request=None):
    """
    Make the member ``membership_id`` the owner of ``organization``.

    Rules (ADR-021):
    * only the current owner or a platform admin may transfer;
    * the target must be an active ``org_admin`` of the org (promote first) whose
      account type is ``developer``; since T-1032b they may already own other
      organizations (the owner membership is canonical);
    * the previous owner(s) stay in the org as ``org_admin``.

    Memberships are set explicitly here and the ``admin_user`` projection follows
    via :func:`sync_admin_projection` (``QuerySet.update``, so the legacy hook that
    would deactivate the previous owner does not run). Returns the new owner membership.
    """
    from apps.core.models import AuditLog
    from apps.organizations.models import Organization

    Membership = _model()
    org = Organization.objects.select_for_update().get(pk=organization.pk)
    caller = actor_role(actor, org.pk)
    if caller not in (OWNER, PLATFORM_ADMIN):
        raise MembershipForbidden('Only the organization owner can transfer ownership.')

    target = (Membership.objects.select_for_update().select_related('user')
              .filter(pk=membership_id, organization=org, is_active=True).first())
    if target is None:
        raise NotFound('Membership not found.')
    if target.role == OWNER:
        raise MembershipConflict('This member already owns the organization.')
    if target.role != ORG_ADMIN:
        raise MembershipInvalid('Ownership can only be transferred to an org admin; '
                                'change the member\'s role to org_admin first.')
    if not target.user.is_active:
        raise MembershipInvalid('The target account is not active.')
    if target.user.role != OWNER_ACCOUNT_TYPE:
        raise MembershipInvalid('The target account must be an organization (developer) '
                                'account to own an organization.')

    previous_admin_id = org.admin_user_id
    previous = list(Membership.objects.select_for_update()
                    .filter(organization=org, role=OWNER, is_active=True)
                    .exclude(pk=target.pk))
    before = {'owner_user_id': str(previous_admin_id) if previous_admin_id else None,
              'owner_membership_ids': [m.pk for m in previous]}

    for m in previous:
        m.role = ORG_ADMIN
        m.save(update_fields=['role'])
    target.role = OWNER
    target.save(update_fields=['role'])
    Organization.objects.filter(pk=org.pk).update(updated_at=timezone.now())
    sync_admin_projection(org.pk)
    # The previous owner's admin_user slot is free again: let one of their other
    # orgs (if any) take the projection back.
    for other_id in (_model().objects.filter(user_id=previous_admin_id, role=OWNER, is_active=True)
                     .values_list('organization_id', flat=True) if previous_admin_id else ()):
        sync_admin_projection(other_id)

    # Keep the caller's instance consistent so a later full save() cannot revert
    # the projection, and its snapshot so the post_save hook sees no change.
    projected = Organization.objects.values_list('admin_user_id', flat=True).get(pk=org.pk)
    organization.admin_user_id = projected
    organization._membership_snapshot = (projected,)

    _audit(actor, AuditLog.Action.UPDATE, org, 'organization.ownership_transferred',
           before=before,
           after={'owner_user_id': str(target.user_id), 'owner_membership_ids': [target.pk]},
           request=request)
    logger.info('organization.ownership_transferred org=%s %s→%s by=%s', org.pk,
                previous_admin_id, target.user_id, getattr(actor, 'pk', None))
    return target


@transaction.atomic
def remove_member(membership, *, actor=None, request=None) -> None:
    """
    Deactivate a membership and clear the agent projection when it points at this org.

    The owner cannot be removed (transfer ownership first). With an ``actor`` the
    grant policy applies (removing an org admin is owner‑only) and the removal is
    audited; ``actor=None`` is for system callers.
    """
    from apps.agents.models import Agent
    from apps.core.models import AuditLog

    if membership.role == OWNER:
        raise MembershipInvalid('Cannot remove the organization owner.')
    if actor is not None:
        caller = actor_role(actor, membership.organization_id)
        _require_manager(caller)
        _require_owner_for(membership.role, caller,
                           'Only the organization owner can remove an org admin.')

    before = _snapshot(membership)
    membership.deactivate()
    _audit(actor, AuditLog.Action.DELETE, membership, 'membership.removed',
           before=before, after=_snapshot(membership), request=request)
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
    ids = owned_organization_ids(user)
    return ids[0] if ids else None


def _agent_org_id(user):
    from apps.agents.models import Agent
    try:
        return user.agent_profile.organization_id
    except (Agent.DoesNotExist, AttributeError):
        return None


# ── Drift detection / repair (manage.py reconcile_memberships) ────────────────

REPAIRABLE = ('missing_owner', 'missing_agent', 'stale_admin_projection', 'unlinked_collaboration')
REPORT_ONLY = ('extra_owner', 'orphan_agent', 'collaboration_without_membership',
               'deprecated_agent_organization_memberships')
# T-1032b: the staged contract migrations refuse while any of these is non-empty.
CONTRACT_BLOCKERS = REPAIRABLE + ('extra_owner', 'collaboration_without_membership',
                                  'deprecated_agent_organization_memberships')


def find_drift() -> dict[str, list]:
    """
    Compare the canonical table with its projections.

    Repairable (by ``repair_drift``):
      missing_owner  — (admin_user, org) for an org with no active ``owner`` membership at all
      missing_agent  — (user, org) where Agent.user + Agent.organization has no active membership
      stale_admin_projection — (owner, org) where admin_user is NULL or another user
                       although the owner could be projected (T-1032b)
      unlinked_collaboration — (agent user, org) of an AgentOrgMembership whose
                       ``membership`` link is unset while a membership exists
    Report only (need a human decision, see ADR-013):
      extra_owner    — orgs with more than one active owner membership
      orphan_agent   — active agent/freelance_agent memberships not backed by
                       Agent.organization or an active AgentOrgMembership
      collaboration_without_membership — active AgentOrgMembership whose agent user
                       has no active membership in that org
      deprecated_agent_organization_memberships — rows in the unused
                       AgentOrganizationMembership table
    """
    from django.db import connection

    from apps.agents.models import Agent, AgentOrgMembership
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

    owners: dict = {}
    for (u, o), r in active.items():
        if r == OWNER:
            owners.setdefault(o, []).append(u)
    projected_users = set(admins.values())
    stale = []
    for o, users in owners.items():
        if len(users) != 1:
            continue
        u = users[0]
        if admins.get(o) == u:
            continue
        if admins.get(o) is None and u in projected_users:
            continue                                   # projected into another org (allowed)
        stale.append((str(u), str(o)))
    member_ids = set(Membership.objects.values_list('user_id', 'organization_id'))
    unlinked = sorted(
        (str(u), str(o)) for u, o in AgentOrgMembership.objects
        .filter(membership__isnull=True, agent__user__isnull=False)
        .values_list('agent__user_id', 'organization_id') if (u, o) in member_ids)
    with connection.cursor() as cur:
        cur.execute("SELECT to_regclass('agent_organization_memberships') IS NOT NULL")
        legacy_table = cur.fetchone()[0]
        deprecated = []
        if legacy_table:
            cur.execute('SELECT p.user_id, m.organization_id FROM agent_organization_memberships m '
                        'JOIN freelance_agent_profiles p ON p.id = m.freelance_agent_id')
            deprecated = sorted((str(f), str(o)) for f, o in cur.fetchall())

    return {
        # Owner membership is canonical (T-1032b): only an org with *no* owner is
        # repaired from admin_user; a mismatch is stale_admin_projection instead.
        'missing_owner': sorted(
            (str(u), str(o)) for o, u in admins.items() if o not in owners),
        'missing_agent': sorted(
            (str(u), str(o)) for u, o in agent_links if (u, o) not in active),
        'stale_admin_projection': sorted(stale),
        'unlinked_collaboration': unlinked,
        'extra_owner': sorted(
            (str(u), str(o)) for o, users in owners.items() if len(users) > 1 for u in users),
        'orphan_agent': sorted(
            (str(u), str(o)) for (u, o), r in active.items()
            if r in ('agent', 'freelance_agent')
            and (u, o) not in agent_links and (u, o) not in collab_links),
        'collaboration_without_membership': sorted(
            (str(u), str(o)) for u, o in collab_links if (u, o) not in active),
        'deprecated_agent_organization_memberships': deprecated,
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
    for _user_id, org_id in drift.get('stale_admin_projection', ()):
        sync_admin_projection(org_id)
    if drift.get('unlinked_collaboration'):
        from apps.agents.models import AgentOrgMembership
        for collab in AgentOrgMembership.objects.filter(membership__isnull=True).select_related('agent'):
            collab.link_membership(save=True)
    return {k: len(drift.get(k, ())) for k in REPAIRABLE}
