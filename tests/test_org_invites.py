"""
T-1032c (KI-041) — org invitations: pending until the invitee accepts; notify
via the outbox; accept/decline/revoke/expiry; grant policy re-checked at
acceptance; only the invitee can respond; every step audited.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import AuditLog, OutboxMessage
from apps.organizations.models import OrganizationInvite as Invite, OrganizationMembership as M
from tests.factories import make_developer, make_membership, make_user

MEMBERS = '/api/v1/organizations/me/members/'
MY = '/api/v1/organizations/invites/'
ORG_INVITES = '/api/v1/organizations/me/invites/'


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class InviteFlowTest(TestCase):
    def setUp(self):
        self.owner, self.org = make_developer()
        self.invitee = make_user(role='agent')

    def _invite(self, role='agent', actor=None):
        r = _api(actor or self.owner).post(MEMBERS, {'phone': self.invitee.phone, 'role': role},
                                           format='json')
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def test_pending_grants_nothing_and_notifies(self):
        data = self._invite()
        self.assertEqual(data['status'], 'pending')
        self.assertFalse(M.objects.filter(user=self.invitee, organization=self.org).exists())
        self.assertEqual(OutboxMessage.objects.filter(idempotency_key=f"org_invite:{data['id']}").count(), 1)
        self.assertTrue(AuditLog.objects.filter(detail='membership.invite_created').exists())
        # The invitee sees it; the org's managers see it.
        self.assertEqual([i['id'] for i in _api(self.invitee).get(MY).data], [data['id']])
        self.assertEqual([i['id'] for i in _api(self.owner).get(ORG_INVITES).data], [data['id']])

    def test_accept_creates_membership(self):
        data = self._invite(role='viewer')
        r = _api(self.invitee).post(f"{MY}{data['id']}/accept/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['invite']['status'], 'accepted')
        m = M.objects.get(user=self.invitee, organization=self.org)
        self.assertEqual((m.role, m.is_active), ('viewer', True))
        self.assertTrue(AuditLog.objects.filter(detail='membership.invite_accepted',
                                                actor=self.invitee).exists())
        # A second response is refused.
        self.assertEqual(_api(self.invitee).post(f"{MY}{data['id']}/decline/").status_code, 409)

    def test_decline(self):
        data = self._invite()
        r = _api(self.invitee).post(f"{MY}{data['id']}/decline/")
        self.assertEqual((r.status_code, r.data['invite']['status'], r.data['membership']),
                         (200, 'declined', None))
        self.assertFalse(M.objects.filter(user=self.invitee, organization=self.org).exists())
        self._invite()                                    # can be invited again

    def test_only_invitee_can_respond(self):
        data = self._invite()
        stranger = make_user(role='agent')
        self.assertEqual(_api(stranger).post(f"{MY}{data['id']}/accept/").status_code, 404)
        self.assertEqual(_api(self.owner).post(f"{MY}{data['id']}/accept/").status_code, 404)
        self.assertEqual(_api(stranger).get(MY).data, [])
        self.assertEqual(APIClient().post(f"{MY}{data['id']}/accept/").status_code, 401)

    def test_duplicate_pending_and_existing_member(self):
        self._invite()
        r = _api(self.owner).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'agent'}, format='json')
        self.assertEqual(r.status_code, 409)
        member = make_user(role='agent')
        make_membership(member, self.org, role='agent')
        r = _api(self.owner).post(MEMBERS, {'phone': member.phone, 'role': 'agent'}, format='json')
        self.assertEqual(r.status_code, 409)

    def test_expiry(self):
        data = self._invite()
        Invite.objects.filter(pk=data['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(_api(self.invitee).get(MY).data, [])
        r = _api(self.invitee).post(f"{MY}{data['id']}/accept/")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Invite.objects.get(pk=data['id']).status, 'expired')
        self._invite()                                    # a lapsed invite does not block a new one

    @override_settings(ORG_INVITE_TTL_DAYS=2)
    def test_ttl_setting(self):
        data = self._invite()
        inv = Invite.objects.get(pk=data['id'])
        self.assertAlmostEqual((inv.expires_at - inv.created_at).total_seconds(), 2 * 86400, delta=5)

    def test_revoke(self):
        data = self._invite()
        viewer = make_user(role='agent')
        make_membership(viewer, self.org, role='viewer')
        self.assertEqual(_api(viewer).delete(f"{ORG_INVITES}{data['id']}/").status_code, 403)
        self.assertEqual(_api(self.owner).delete(f"{ORG_INVITES}{data['id']}/").status_code, 204)
        self.assertEqual(_api(self.invitee).post(f"{MY}{data['id']}/accept/").status_code, 409)
        _, other_org = make_developer(org_name='Other')
        self.assertEqual(_api(self.owner).delete(f"{ORG_INVITES}999999/").status_code, 404)

    def test_inviter_losing_rights_voids_invite(self):
        admin = make_user(role='developer')
        admin_m = make_membership(admin, self.org, role='org_admin')
        data = self._invite(actor=admin)
        admin_m.deactivate()                             # inviter left the org
        r = _api(self.invitee).post(f"{MY}{data['id']}/accept/")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Invite.objects.get(pk=data['id']).status, 'revoked')
        self.assertFalse(M.objects.filter(user=self.invitee, organization=self.org, is_active=True).exists())

    def test_grant_policy_still_applies_at_invite_time(self):
        r = _api(self.owner).post(MEMBERS, {'phone': self.invitee.phone, 'role': 'owner'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Invite.objects.exists())

    def test_non_manager_cannot_list_org_invites(self):
        viewer = make_user(role='agent')
        make_membership(viewer, self.org, role='viewer')
        self.assertEqual(_api(viewer).get(ORG_INVITES).status_code, 403)
