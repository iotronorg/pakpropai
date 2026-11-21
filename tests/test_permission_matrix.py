"""
T-1031a / ADR-020 — membership-based RBAC, active org context, grant policy.

The matrix pins, for each actor, what the org-administration endpoints and an
org-scoped data endpoint return. It is the regression net for moving the
remaining legacy readers (T-1031b).

Actors (org A unless stated):
  platform_admin · owner (developer, A.admin_user) · org_admin (membership
  role, user.role=agent) · agent · viewer · other_owner (owner of B) ·
  client · anonymous
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.context import INVALID_ORG, set_requested_org_id
from apps.core.permissions import get_user_org
from apps.organizations.models import OrganizationMembership
from tests.factories import (
    make_agent, make_client, make_developer, make_lead, make_membership, make_org, make_user,
)

MEMBERS = '/api/v1/organizations/me/members/'
LEADS = '/api/v1/leads/'
ROLE = OrganizationMembership.Role


class _World(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.owner, cls.org = make_developer(org_name='Org A')
        cls.other_owner, cls.org_b = make_developer(org_name='Org B')
        cls.org_c = make_org(name='Org C')

        cls.org_admin = make_user(role='agent')
        make_membership(cls.org_admin, cls.org, role=ROLE.ORG_ADMIN)

        cls.agent_user, cls.agent = make_agent(org=cls.org)
        cls.viewer = make_user(role='agent')
        make_membership(cls.viewer, cls.org, role=ROLE.VIEWER)

        cls.platform_admin = make_user(role='admin')
        cls.client_user = make_client()
        cls.invitee = make_client()

        cls.lead_a = make_lead(make_client(), org=cls.org)
        cls.lead_b = make_lead(make_client(), org=cls.org_b)

    def api(self, user=None, org_header=None):
        c = APIClient()
        if user is not None:
            c.force_authenticate(user)
        if org_header is not None:
            c.credentials(HTTP_X_ORGANIZATION_ID=org_header)
        return c


class MembersListMatrixTest(_World):

    def test_matrix(self):
        expected = {
            'owner': 200, 'org_admin': 200, 'agent': 403, 'viewer': 403,
            'other_owner': 200, 'client': 403,
            # Platform admins pass the role gate but have no org of their own.
            'platform_admin': 403,
        }
        users = {'owner': self.owner, 'org_admin': self.org_admin, 'agent': self.agent_user,
                 'viewer': self.viewer, 'other_owner': self.other_owner, 'client': self.client_user,
                 'platform_admin': self.platform_admin}
        for actor, code in expected.items():
            with self.subTest(actor=actor):
                self.assertEqual(self.api(users[actor]).get(MEMBERS).status_code, code)
        self.assertEqual(self.api().get(MEMBERS).status_code, 401)

    def test_each_admin_sees_only_their_own_org(self):
        phones_a = {m['user_phone'] for m in self.api(self.org_admin).get(MEMBERS).json()}
        phones_b = {m['user_phone'] for m in self.api(self.other_owner).get(MEMBERS).json()}
        self.assertIn(self.agent_user.phone, phones_a)
        self.assertNotIn(self.agent_user.phone, phones_b)
        self.assertEqual(phones_b, {self.other_owner.phone})


class GrantPolicyTest(_World):

    def _invite(self, actor, role):
        return self.api(actor).post(MEMBERS, {'phone': self.invitee.phone, 'role': role}, format='json')

    def test_nobody_can_grant_owner(self):
        for actor in (self.owner, self.org_admin):
            with self.subTest(actor=actor.phone):
                resp = self._invite(actor, ROLE.OWNER)
                self.assertEqual(resp.status_code, 403)
        self.assertFalse(OrganizationMembership.objects.filter(user=self.invitee).exists())

    def test_only_owner_grants_org_admin(self):
        self.assertEqual(self._invite(self.org_admin, ROLE.ORG_ADMIN).status_code, 403)
        self.assertEqual(self._invite(self.owner, ROLE.ORG_ADMIN).status_code, 201)

    def test_org_admin_can_invite_lower_roles(self):
        resp = self._invite(self.org_admin, ROLE.AGENT)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['role'], ROLE.AGENT)

    def test_reactivation_path_obeys_the_same_rules(self):
        m = make_membership(self.invitee, self.org, role=ROLE.AGENT)
        m.is_active = False
        m.save(update_fields=['is_active'])
        self.assertEqual(self._invite(self.org_admin, ROLE.ORG_ADMIN).status_code, 403)
        m.refresh_from_db()
        self.assertFalse(m.is_active)

    def test_agents_and_viewers_cannot_invite(self):
        for actor in (self.agent_user, self.viewer):
            with self.subTest(actor=actor.phone):
                self.assertEqual(self._invite(actor, ROLE.AGENT).status_code, 403)

    def test_only_owner_removes_org_admin(self):
        target = make_user(role='agent')
        m = make_membership(target, self.org, role=ROLE.ORG_ADMIN)
        self.assertEqual(self.api(self.org_admin).delete(f'{MEMBERS}{m.pk}/').status_code, 403)
        self.assertEqual(self.api(self.owner).delete(f'{MEMBERS}{m.pk}/').status_code, 204)

    def test_owner_cannot_be_removed(self):
        owner_m = OrganizationMembership.objects.get(user=self.owner, organization=self.org)
        self.assertEqual(self.api(self.org_admin).delete(f'{MEMBERS}{owner_m.pk}/').status_code, 400)

    def test_cannot_remove_member_of_another_org(self):
        other_m = OrganizationMembership.objects.get(user=self.other_owner, organization=self.org_b)
        self.assertEqual(self.api(self.owner).delete(f'{MEMBERS}{other_m.pk}/').status_code, 404)


class ActiveOrgContextTest(_World):
    """A user who owns B and is org_admin of A switches with X-Organization-ID."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        make_membership(cls.other_owner, cls.org, role=ROLE.ORG_ADMIN)

    def _phones(self, header=None):
        resp = self.api(self.other_owner, header).get(MEMBERS)
        return resp.status_code, {m['user_phone'] for m in resp.json()} if resp.status_code == 200 else None

    def test_default_is_primary_org(self):
        code, phones = self._phones()
        self.assertEqual(code, 200)
        self.assertNotIn(self.agent_user.phone, phones)      # B, the owned org

    def test_header_selects_a_member_org(self):
        code, phones = self._phones(str(self.org.pk))
        self.assertEqual(code, 200)
        self.assertIn(self.agent_user.phone, phones)

    def test_header_for_non_member_org_denies_and_is_recorded(self):
        from apps.security.models import ApiSecurityEvent
        self.assertEqual(self._phones(str(self.org_c.pk))[0], 403)
        events = ApiSecurityEvent.objects.filter(event_type='cross_tenant', user_id=str(self.other_owner.pk))
        self.assertEqual(events.count(), 1)                    # once per request
        self.assertIn(str(self.org_c.pk), events.get().threat_detail)

    def test_malformed_header_denies_instead_of_falling_back(self):
        self.assertEqual(self._phones('not-a-uuid')[0], 403)

    def test_role_comes_from_the_selected_org(self):
        # As org_admin of A (not owner) the owner-only grant is refused there…
        resp = self.api(self.other_owner, str(self.org.pk)).post(
            MEMBERS, {'phone': self.invitee.phone, 'role': ROLE.ORG_ADMIN}, format='json')
        self.assertEqual(resp.status_code, 403)
        # …but allowed in B, which they own.
        resp = self.api(self.other_owner, str(self.org_b.pk)).post(
            MEMBERS, {'phone': self.invitee.phone, 'role': ROLE.ORG_ADMIN}, format='json')
        self.assertEqual(resp.status_code, 201)

    def test_org_scoped_data_follows_header(self):
        # Developer lead list is scoped by get_user_org().
        ids = lambda h: {r['id'] for r in self.api(self.other_owner, h).get(LEADS).json()['results']}
        self.assertEqual(ids(None), {str(self.lead_b.pk)})
        self.assertEqual(ids(str(self.org.pk)), {str(self.lead_a.pk)})

    def test_context_is_cleared_after_request(self):
        self.api(self.other_owner, str(self.org.pk)).get(MEMBERS)
        self.assertEqual(get_user_org(self.other_owner), self.org_b)

    def test_helper_semantics(self):
        try:
            set_requested_org_id(INVALID_ORG)
            self.assertIsNone(get_user_org(self.owner))
            set_requested_org_id(str(self.org_c.pk))
            self.assertIsNone(get_user_org(self.owner))
        finally:
            set_requested_org_id('')
        self.assertEqual(get_user_org(self.owner), self.org)

    def test_deactivated_membership_loses_access_immediately(self):
        m = OrganizationMembership.objects.get(user=self.other_owner, organization=self.org)
        m.is_active = False
        m.save(update_fields=['is_active'])
        self.assertEqual(self._phones(str(self.org.pk))[0], 403)


class OtpPurposeTest(TestCase):
    """KI-030."""

    def test_only_login_and_registration_purposes(self):
        api = APIClient()
        for i, (purpose, code) in enumerate([
            (None, 200), ('otp_login', 200), ('registration_verify', 200),
            ('password_reset', 400), ('org_registration', 400), ('anything', 400),
        ]):
            with self.subTest(purpose=purpose):
                body = {'phone': f'+92300555{i:04d}'}
                if purpose:
                    body['purpose'] = purpose
                resp = api.post('/api/v1/auth/otp/send/', body, format='json', REMOTE_ADDR=f'10.60.0.{i + 1}')
                self.assertEqual(resp.status_code, code, resp.content)
