"""
T-1032a / ADR-021 — membership contract: invite, role change, removal and
ownership transfer go through ``apps.organizations.membership`` with the
ADR-020 grant policy and an ``AuditLog`` row per change.
"""
import uuid

from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import AuditLog
from apps.core.permissions import get_admin_org, get_user_org
from apps.organizations import membership as svc
from apps.organizations.models import Organization, OrganizationMembership
from tests.factories import make_agent, make_developer, make_membership, make_user

ROLE = OrganizationMembership.Role
MEMBERS = '/api/v1/organizations/me/members/'
TRANSFER = '/api/v1/organizations/me/ownership/transfer/'


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _audits(target, detail=None):
    qs = AuditLog.objects.filter(target_model=target.__class__.__name__, target_id=str(target.pk))
    if detail:
        qs = qs.filter(detail=detail)
    return qs


class _World(TestCase):
    def setUp(self):
        self.owner, self.org = make_developer(org_name='Org A')
        self.owner_m = OrganizationMembership.objects.get(user=self.owner, organization=self.org)
        self.admin_user = make_user(role='developer')
        self.admin_m = make_membership(self.admin_user, self.org, role=ROLE.ORG_ADMIN)
        self.agent_user, self.agent = make_agent(org=self.org)
        self.agent_m = OrganizationMembership.objects.get(user=self.agent_user, organization=self.org)
        self.viewer = make_user(role='agent')
        self.viewer_m = make_membership(self.viewer, self.org, role=ROLE.VIEWER)
        self.other_owner, self.org_b = make_developer(org_name='Org B')
        self.platform_admin = make_user(role='admin')
        self.invitee = make_user(role='agent')

    def refresh(self, *objs):
        for o in objs:
            o.refresh_from_db()


class InviteContractTest(_World):
    def test_invite_is_audited(self):
        r = _api(self.owner).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'agent'})
        self.assertEqual(r.status_code, 201)
        m = OrganizationMembership.objects.get(user=self.invitee, organization=self.org)
        log = _audits(m, 'membership.invited').get()
        self.assertEqual(log.actor, self.owner)
        self.assertEqual(log.action, AuditLog.Action.CREATE)
        self.assertIsNone(log.before)
        self.assertEqual(log.after['role'], 'agent')
        self.assertNotIn(self.invitee.phone, str(log.after))

    def test_reactivation_is_audited(self):
        m = make_membership(self.invitee, self.org, role=ROLE.VIEWER)
        m.deactivate()
        r = _api(self.owner).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'agent'})
        self.assertEqual(r.status_code, 201)
        log = _audits(m, 'membership.reactivated').get()
        self.assertFalse(log.before['is_active'])
        self.assertEqual(log.after['role'], 'agent')

    def test_owner_never_grantable(self):
        r = _api(self.owner).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'owner'})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(AuditLog.objects.exists())

    def test_org_admin_grant_is_owner_only(self):
        r = _api(self.admin_user).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'org_admin'})
        self.assertEqual(r.status_code, 403)
        r = _api(self.owner).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'org_admin'})
        self.assertEqual(r.status_code, 201)

    def test_denied_invite_does_not_reveal_unknown_phone(self):
        r = _api(self.admin_user).post(MEMBERS, {'phone': '+15550000000', 'role': 'org_admin'})
        self.assertEqual(r.status_code, 403)

    def test_service_rejects_non_manager(self):
        with self.assertRaises(svc.MembershipForbidden):
            svc.invite_member(self.viewer, self.org, self.invitee, ROLE.AGENT)
        self.assertFalse(OrganizationMembership.objects.filter(user=self.invitee).exists())

    def test_duplicate_is_409_and_not_audited(self):
        r = _api(self.owner).post(MEMBERS, {'phone': self.viewer.phone, 'role': 'agent'})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['error_info']['code'], 'membership_conflict')
        self.assertFalse(AuditLog.objects.exists())

    def test_concurrent_invite_is_409_not_500(self):
        from unittest import mock
        from django.db import IntegrityError
        with mock.patch.object(OrganizationMembership.objects, 'create', side_effect=IntegrityError):
            with self.assertRaises(svc.MembershipConflict):
                svc.invite_member(self.owner, self.org, self.invitee, ROLE.AGENT)
        self.assertFalse(AuditLog.objects.exists())



class RoleChangeContractTest(_World):
    def patch(self, actor, m, **body):
        return _api(actor).patch(f'{MEMBERS}{m.pk}/', body, format='json')

    def test_org_admin_changes_agent_role(self):
        r = self.patch(self.admin_user, self.agent_m, role='viewer')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['role'], 'viewer')
        self.refresh(self.agent_m)
        self.assertEqual(self.agent_m.role, ROLE.VIEWER)
        log = _audits(self.agent_m, 'membership.role_changed').get()
        self.assertEqual((log.before['role'], log.after['role']), ('agent', 'viewer'))
        self.assertEqual(log.actor, self.admin_user)

    def test_employment_type_change(self):
        r = self.patch(self.owner, self.agent_m, role='agent', employment_type='freelance')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['employment_type'], 'freelance')

    def test_same_role_is_noop(self):
        r = self.patch(self.owner, self.agent_m, role='agent')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(_audits(self.agent_m).exists())

    def test_owner_not_grantable_by_role_change(self):
        r = self.patch(self.owner, self.admin_m, role='owner')
        self.assertEqual(r.status_code, 403)
        self.refresh(self.admin_m)
        self.assertEqual(self.admin_m.role, ROLE.ORG_ADMIN)

    def test_owner_membership_is_immutable(self):
        with self.assertRaises(svc.MembershipForbidden):
            svc.change_role(self.platform_admin, self.owner_m, ROLE.VIEWER)
        r = self.patch(self.owner, self.owner_m, role='org_admin')
        self.assertEqual(r.status_code, 403)

    def test_org_admin_role_is_owner_only(self):
        # org admin cannot demote another org admin, nor promote someone to org admin
        other_admin = make_user(role='developer')
        other_m = make_membership(other_admin, self.org, role=ROLE.ORG_ADMIN)
        self.assertEqual(self.patch(self.admin_user, other_m, role='viewer').status_code, 403)
        self.assertEqual(self.patch(self.admin_user, self.agent_m, role='org_admin').status_code, 403)
        self.assertEqual(self.patch(self.owner, self.agent_m, role='org_admin').status_code, 200)
        self.assertEqual(self.patch(self.owner, other_m, role='viewer').status_code, 200)

    def test_validation(self):
        self.assertEqual(self.patch(self.owner, self.agent_m, role='superuser').status_code, 400)
        self.assertEqual(self.patch(self.owner, self.agent_m).status_code, 400)
        self.assertEqual(
            self.patch(self.owner, self.agent_m, role='agent', employment_type='x').status_code, 400)

    def test_unknown_or_foreign_membership_is_404(self):
        foreign = OrganizationMembership.objects.get(user=self.other_owner)
        self.assertEqual(self.patch(self.owner, foreign, role='viewer').status_code, 404)
        r = _api(self.owner).patch(f'{MEMBERS}999999/', {'role': 'viewer'}, format='json')
        self.assertEqual(r.status_code, 404)

    def test_viewer_cannot_change_roles(self):
        self.assertEqual(self.patch(self.viewer, self.agent_m, role='viewer').status_code, 403)

    def test_service_rejects_inactive_membership(self):
        self.viewer_m.deactivate()
        with self.assertRaises(svc.MembershipConflict):
            svc.change_role(self.owner, self.viewer_m, ROLE.AGENT)


class RemovalContractTest(_World):
    def delete(self, actor, m):
        return _api(actor).delete(f'{MEMBERS}{m.pk}/')

    def test_removal_is_audited_and_clears_agent_projection(self):
        r = self.delete(self.admin_user, self.agent_m)
        self.assertEqual(r.status_code, 204)
        self.refresh(self.agent_m, self.agent)
        self.assertFalse(self.agent_m.is_active)
        self.assertIsNone(self.agent.organization_id)
        log = _audits(self.agent_m, 'membership.removed').get()
        self.assertEqual(log.action, AuditLog.Action.DELETE)
        self.assertTrue(log.before['is_active'])
        self.assertFalse(log.after['is_active'])

    def test_owner_cannot_be_removed(self):
        with self.assertRaises(svc.MembershipInvalid):
            svc.remove_member(self.owner_m, actor=self.platform_admin)
        r = self.delete(self.owner, self.owner_m)
        self.assertEqual(r.status_code, 400)
        with self.assertRaises(svc.MembershipInvalid):
            svc.remove_member(self.owner_m)

    def test_org_admin_removal_is_owner_only(self):
        self.assertEqual(self.delete(self.admin_user, self.admin_m).status_code, 403)
        self.assertEqual(self.delete(self.owner, self.admin_m).status_code, 204)

    def test_system_removal_without_actor(self):
        svc.remove_member(self.viewer_m)
        self.refresh(self.viewer_m)
        self.assertFalse(self.viewer_m.is_active)
        self.assertIsNone(_audits(self.viewer_m, 'membership.removed').get().actor)


class OwnershipTransferTest(_World):
    def transfer(self, actor, membership_id, url=TRANSFER):
        return _api(actor).post(url, {'membership_id': membership_id}, format='json')

    def test_owner_transfers_to_org_admin(self):
        r = self.transfer(self.owner, self.admin_m.pk)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['owner']['id'], str(self.admin_m.pk))
        self.refresh(self.org, self.owner_m, self.admin_m)
        self.assertEqual(self.org.admin_user_id, self.admin_user.pk)
        self.assertEqual(self.admin_m.role, ROLE.OWNER)
        # previous owner stays in the org as an org admin
        self.assertTrue(self.owner_m.is_active)
        self.assertEqual(self.owner_m.role, ROLE.ORG_ADMIN)
        self.assertEqual(get_user_org(self.owner), self.org)
        self.assertEqual(get_admin_org(self.owner), self.org)
        # projections agree with the table
        drift = svc.find_drift()
        self.assertEqual(drift['missing_owner'], [])
        self.assertEqual(drift['extra_owner'], [])
        log = _audits(self.org, 'organization.ownership_transferred').get()
        self.assertEqual(log.actor, self.owner)
        self.assertEqual(log.before['owner_user_id'], str(self.owner.pk))
        self.assertEqual(log.after['owner_user_id'], str(self.admin_user.pk))

    def test_new_owner_holds_owner_only_rights_and_old_owner_loses_them(self):
        self.assertEqual(self.transfer(self.owner, self.admin_m.pk).status_code, 200)
        r = _api(self.owner).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'org_admin'})
        self.assertEqual(r.status_code, 403)
        r = _api(self.admin_user).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'org_admin'})
        self.assertEqual(r.status_code, 201)
        # the former owner can now be removed by the new owner
        self.refresh(self.owner_m)
        r = _api(self.admin_user).delete(f'{MEMBERS}{self.owner_m.pk}/')
        self.assertEqual(r.status_code, 204)

    def test_stale_instance_save_does_not_revert(self):
        org = Organization.objects.get(pk=self.org.pk)
        svc.transfer_ownership(self.owner, org, self.admin_m.pk)
        org.name = 'Renamed'
        org.save()
        self.refresh(self.org, self.admin_m, self.owner_m)
        self.assertEqual(self.org.admin_user_id, self.admin_user.pk)
        self.assertEqual(self.admin_m.role, ROLE.OWNER)
        self.assertTrue(self.owner_m.is_active)

    def test_only_owner_may_transfer(self):
        other_admin = make_user(role='developer')
        other_m = make_membership(other_admin, self.org, role=ROLE.ORG_ADMIN)
        self.assertEqual(self.transfer(self.admin_user, other_m.pk).status_code, 403)
        self.assertEqual(self.transfer(self.agent_user, self.admin_m.pk).status_code, 403)
        self.refresh(self.org)
        self.assertEqual(self.org.admin_user_id, self.owner.pk)
        self.assertFalse(AuditLog.objects.exists())

    def test_target_must_be_org_admin(self):
        r = self.transfer(self.owner, self.agent_m.pk)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data['error_info']['code'], 'membership_invalid')

    def test_target_account_must_be_developer(self):
        agent_admin = make_user(role='agent')
        m = make_membership(agent_admin, self.org, role=ROLE.ORG_ADMIN)
        self.assertEqual(self.transfer(self.owner, m.pk).status_code, 400)

    def test_target_inactive_account(self):
        self.admin_user.is_active = False
        self.admin_user.save(update_fields=['is_active'])
        self.assertEqual(self.transfer(self.owner, self.admin_m.pk).status_code, 400)

    def test_target_owning_another_org_is_409(self):
        m = make_membership(self.other_owner, self.org, role=ROLE.ORG_ADMIN)
        self.assertEqual(self.transfer(self.owner, m.pk).status_code, 409)

    def test_self_transfer_is_409(self):
        self.assertEqual(self.transfer(self.owner, self.owner_m.pk).status_code, 409)

    def test_inactive_or_foreign_target_is_404(self):
        foreign = OrganizationMembership.objects.get(user=self.other_owner)
        self.assertEqual(self.transfer(self.owner, foreign.pk).status_code, 404)
        self.admin_m.deactivate()
        self.assertEqual(self.transfer(self.owner, self.admin_m.pk).status_code, 404)

    def test_bad_membership_id_is_400(self):
        self.assertEqual(self.transfer(self.owner, 'abc').status_code, 400)
        r = _api(self.owner).post(TRANSFER, {}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_platform_admin_path(self):
        url = f'/api/v1/organizations/{self.org.pk}/ownership/transfer/'
        self.assertEqual(self.transfer(self.owner, self.admin_m.pk, url).status_code, 403)
        r = self.transfer(self.platform_admin, self.admin_m.pk, url)
        self.assertEqual(r.status_code, 200)
        self.refresh(self.org)
        self.assertEqual(self.org.admin_user_id, self.admin_user.pk)
        unknown = f'/api/v1/organizations/{uuid.uuid4()}/ownership/transfer/'
        self.assertEqual(self.transfer(self.platform_admin, self.admin_m.pk, unknown).status_code, 404)

    def test_org_header_selects_the_org_to_transfer(self):
        # the owner of B is an org admin of A; selecting A gives no transfer right
        make_membership(self.other_owner, self.org, role=ROLE.ORG_ADMIN)
        c = _api(self.other_owner)
        c.credentials(HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(c.post(TRANSFER, {'membership_id': self.admin_m.pk},
                                format='json').status_code, 403)

    def test_unauthenticated(self):
        self.assertEqual(APIClient().post(TRANSFER, {'membership_id': 1}).status_code, 401)


class HighRiskDealAuditTest(TestCase):
    """KI-040: the escrow high-risk audit row used fields AuditLog does not have."""

    def test_audit_row_written(self):
        from apps.escrow.views import _check_high_risk_deal

        class EscrowDeal:
            pk = id = uuid.uuid4()
            buyer_id = None

        class Prop:
            id = uuid.uuid4()
            ai_score = 20
            risk_level = 'HIGH'

        deal = EscrowDeal()
        _check_high_risk_deal(Prop(), deal)
        log = AuditLog.objects.get(target_model='EscrowDeal', target_id=str(deal.pk))
        self.assertEqual(log.detail, 'high_risk_deal_lock')
        self.assertEqual(log.after['ai_score'], 20)
