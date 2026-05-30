"""
Tests for GET/POST/DELETE /api/v1/organizations/me/members/
"""
from django.test import TestCase
from rest_framework.test import APIClient
from apps.organizations.models import Organization, OrganizationMembership
from apps.users.models import User


def _user(phone, role='developer', name='Test'):
    u = User.objects.create_user(phone=phone, name=name, role=role)
    u.is_phone_verified = True
    u.save(update_fields=['is_phone_verified'])
    return u


def _org(admin_user, name='Test Org', country='PK'):
    return Organization.objects.create(
        name=name,
        admin_user=admin_user,
        country=country,
        phone=admin_user.phone,
    )


class OrgMembershipListTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _user('+923001111111', role='developer', name='Owner')
        self.org = _org(self.owner)
        self.client.force_authenticate(user=self.owner)
        # seed an owner membership (created at org registration normally)
        OrganizationMembership.objects.create(
            user=self.owner,
            organization=self.org,
            role=OrganizationMembership.Role.OWNER,
        )

    def test_get_members_returns_list(self):
        r = self.client.get('/api/v1/organizations/me/members/')
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.data, list)
        self.assertEqual(len(r.data), 1)
        self.assertEqual(r.data[0]['role'], 'owner')

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        r = self.client.get('/api/v1/organizations/me/members/')
        self.assertEqual(r.status_code, 401)

    def test_agent_role_returns_403(self):
        agent_user = _user('+923002222222', role='agent', name='Agent')
        self.client.force_authenticate(user=agent_user)
        r = self.client.get('/api/v1/organizations/me/members/')
        self.assertEqual(r.status_code, 403)


class OrgMembershipInviteTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _user('+923003333333', role='developer', name='Owner')
        self.org = _org(self.owner)
        self.client.force_authenticate(user=self.owner)
        OrganizationMembership.objects.create(
            user=self.owner,
            organization=self.org,
            role=OrganizationMembership.Role.OWNER,
        )
        self.invited = _user('+923004444444', role='agent', name='Invited')

    def test_invite_existing_user(self):
        r = self.client.post('/api/v1/organizations/me/members/', {
            'phone': '+923004444444',
            'role': 'agent',
            'employment_type': 'internal',
        })
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['user_phone'], '+923004444444')
        self.assertEqual(r.data['role'], 'agent')
        self.assertTrue(OrganizationMembership.objects.filter(user=self.invited, organization=self.org).exists())

    def test_invite_nonexistent_phone_returns_404(self):
        r = self.client.post('/api/v1/organizations/me/members/', {
            'phone': '+923009999999',
            'role': 'agent',
            'employment_type': 'internal',
        })
        self.assertEqual(r.status_code, 404)

    def test_invite_duplicate_returns_409(self):
        OrganizationMembership.objects.create(
            user=self.invited,
            organization=self.org,
            role=OrganizationMembership.Role.AGENT,
        )
        r = self.client.post('/api/v1/organizations/me/members/', {
            'phone': '+923004444444',
            'role': 'agent',
            'employment_type': 'internal',
        })
        self.assertEqual(r.status_code, 409)

    def test_invite_invalid_role_returns_400(self):
        r = self.client.post('/api/v1/organizations/me/members/', {
            'phone': '+923004444444',
            'role': 'superuser',
            'employment_type': 'internal',
        })
        self.assertEqual(r.status_code, 400)

    def test_cross_org_isolation(self):
        other_owner = _user('+923005555555', role='developer', name='Other Owner')
        other_org = _org(other_owner, name='Other Org')
        OrganizationMembership.objects.create(
            user=other_owner,
            organization=other_org,
            role=OrganizationMembership.Role.OWNER,
        )
        self.client.force_authenticate(user=other_owner)
        r = self.client.get('/api/v1/organizations/me/members/')
        self.assertEqual(r.status_code, 200)
        phones = [m['user_phone'] for m in r.data]
        self.assertNotIn('+923003333333', phones)


class OrgMembershipRemoveTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = _user('+923006666666', role='developer', name='Owner')
        self.org = _org(self.owner)
        self.client.force_authenticate(user=self.owner)
        self.owner_membership = OrganizationMembership.objects.create(
            user=self.owner,
            organization=self.org,
            role=OrganizationMembership.Role.OWNER,
        )
        self.member = _user('+923007777777', role='agent', name='Member')
        self.membership = OrganizationMembership.objects.create(
            user=self.member,
            organization=self.org,
            role=OrganizationMembership.Role.AGENT,
        )

    def test_remove_member(self):
        r = self.client.delete(f'/api/v1/organizations/me/members/{self.membership.id}/')
        self.assertEqual(r.status_code, 204)
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active)

    def test_cannot_remove_owner(self):
        r = self.client.delete(f'/api/v1/organizations/me/members/{self.owner_membership.id}/')
        self.assertEqual(r.status_code, 400)

    def test_remove_nonexistent_returns_404(self):
        r = self.client.delete('/api/v1/organizations/me/members/99999/')
        self.assertEqual(r.status_code, 404)
