import pytest
from django.test import TestCase
from django.core.exceptions import ValidationError
from apps.users.models import User
from apps.organizations.models import OrganizationMembership, Organization
from apps.agents.models import FreelanceAgentProfile, AgentOrganizationMembership
from apps.leads.models import CRMContact, ClientProfile


class FreelanceAgentProfileTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(phone='+441234567895', role='agent')
        self.org = Organization.objects.create(name='Test Org', slug='test-org', country='GB')

    def test_freelance_agent_profile_creation(self):
        profile = FreelanceAgentProfile.objects.create(
            user=self.user,
            verification_status='unverified',
        )
        self.assertEqual(profile.user, self.user)
        self.assertEqual(profile.verification_status, 'unverified')

    def test_agent_org_membership_creation(self):
        profile = FreelanceAgentProfile.objects.create(user=self.user)
        membership = AgentOrganizationMembership.objects.create(
            freelance_agent=profile,
            organization=self.org,
            commission_type='percentage',
            commission_rate='5.00',
        )
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.commission_type, 'percentage')

    def test_unique_freelance_agent_per_org(self):
        profile = FreelanceAgentProfile.objects.create(user=self.user)
        AgentOrganizationMembership.objects.create(
            freelance_agent=profile, organization=self.org,
            commission_type='flat', commission_rate='0',
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            AgentOrganizationMembership.objects.create(
                freelance_agent=profile, organization=self.org,
                commission_type='flat', commission_rate='0',
            )


class UserPlatformRoleTest(TestCase):
    def test_platform_role_field_exists(self):
        u = User(phone='+441234567890', role='admin', platform_role='super_admin')
        self.assertEqual(u.platform_role, 'super_admin')

    def test_platform_role_null_for_non_admin(self):
        u = User(phone='+441234567891', role='agent', platform_role=None)
        self.assertIsNone(u.platform_role)

    def test_invalid_platform_role_rejected(self):
        u = User.objects.create(phone='+441234567892', role='admin', platform_role='invalid_role')
        with self.assertRaises(Exception):
            u.full_clean()

    def test_non_admin_cannot_have_platform_role(self):
        """clean() must reject valid platform_role on a non-admin user."""
        u = User.objects.create(phone='+441234567893', role='agent', platform_role='ops_admin')
        with self.assertRaises(ValidationError) as ctx:
            u.full_clean()
        self.assertIn('platform_role', ctx.exception.message_dict)


class OrganizationMembershipRolesTest(TestCase):
    def test_all_org_roles_defined(self):
        role_values = [r.value for r in OrganizationMembership.Role]
        expected = [
            'owner', 'org_admin', 'team_manager', 'sales_manager',
            'crm_operator', 'agent', 'freelance_agent', 'viewer',
        ]
        for role in expected:
            self.assertIn(role, role_values, f"Role '{role}' not in OrganizationMembership.Role")


class CRMContactTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='CRM Org', slug='crm-org', country='AE')

    def test_crm_contact_creation(self):
        contact = CRMContact.objects.create(
            organization=self.org,
            phone='+971501234567',
            name='Ahmed Al-Rashid',
            source='whatsapp',
        )
        self.assertEqual(contact.organization, self.org)
        self.assertEqual(contact.phone, '+971501234567')

    def test_client_profile_creation(self):
        contact = CRMContact.objects.create(
            organization=self.org, phone='+971501234568', source='whatsapp',
        )
        profile = ClientProfile.objects.create(
            contact=contact,
            wa_phone_number='+971501234568',
            preferred_lang='ar',
        )
        self.assertEqual(profile.contact, contact)
        self.assertEqual(profile.preferred_lang, 'ar')

    def test_unique_org_phone_constraint(self):
        CRMContact.objects.create(organization=self.org, phone='+971501234569', source='manual')
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            CRMContact.objects.create(organization=self.org, phone='+971501234569', source='manual')


class PlatformPermissionTest(TestCase):
    def _make_request(self, role, platform_role=None):
        from unittest.mock import MagicMock
        user = MagicMock()
        user.is_authenticated = True
        user.role = role
        user.platform_role = platform_role
        req = MagicMock()
        req.user = user
        return req

    def test_super_admin_passes_all_platform_checks(self):
        from apps.core.permissions import (
            IsSuperAdmin, IsOpsAdmin, IsAIAdmin,
            IsComplianceAdmin, IsBillingAdmin,
        )
        req = self._make_request('admin', 'super_admin')
        self.assertTrue(IsSuperAdmin().has_permission(req, None))
        self.assertTrue(IsOpsAdmin().has_permission(req, None))
        self.assertTrue(IsAIAdmin().has_permission(req, None))
        self.assertTrue(IsComplianceAdmin().has_permission(req, None))
        self.assertTrue(IsBillingAdmin().has_permission(req, None))

    def test_billing_admin_only_passes_billing_check(self):
        from apps.core.permissions import (
            IsSuperAdmin, IsOpsAdmin, IsBillingAdmin,
        )
        req = self._make_request('admin', 'billing_admin')
        self.assertFalse(IsSuperAdmin().has_permission(req, None))
        self.assertFalse(IsOpsAdmin().has_permission(req, None))
        self.assertTrue(IsBillingAdmin().has_permission(req, None))

    def test_non_admin_role_fails_all_platform_checks(self):
        from apps.core.permissions import (
            IsSuperAdmin, IsBillingAdmin,
        )
        req = self._make_request('developer')
        self.assertFalse(IsSuperAdmin().has_permission(req, None))
        self.assertFalse(IsBillingAdmin().has_permission(req, None))


class OrgMembershipPermissionTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test', slug='test2', country='PK')
        self.owner_user = User.objects.create(phone='+923001111111', role='developer')
        self.agent_user = User.objects.create(phone='+923002222222', role='agent')
        self.viewer_user = User.objects.create(phone='+923003333333', role='developer')
        OrganizationMembership.objects.create(
            user=self.owner_user, organization=self.org, role='owner')
        OrganizationMembership.objects.create(
            user=self.agent_user, organization=self.org, role='agent')
        OrganizationMembership.objects.create(
            user=self.viewer_user, organization=self.org, role='viewer')

    def _make_request(self, user):
        from unittest.mock import MagicMock
        req = MagicMock()
        req.user = user
        return req

    def test_owner_passes_all_org_checks(self):
        from apps.core.permissions import (
            IsOrgOwner, IsOrgAdminMembership, IsAgentOrAbove,
        )
        req = self._make_request(self.owner_user)
        self.assertTrue(IsOrgOwner().has_object_permission(req, None, self.org))
        self.assertTrue(IsOrgAdminMembership().has_object_permission(req, None, self.org))
        self.assertTrue(IsAgentOrAbove().has_object_permission(req, None, self.org))

    def test_agent_passes_agent_check_only(self):
        from apps.core.permissions import (
            IsOrgOwner, IsOrgAdminMembership, IsAgentOrAbove,
        )
        req = self._make_request(self.agent_user)
        self.assertFalse(IsOrgOwner().has_object_permission(req, None, self.org))
        self.assertFalse(IsOrgAdminMembership().has_object_permission(req, None, self.org))
        self.assertTrue(IsAgentOrAbove().has_object_permission(req, None, self.org))

    def test_viewer_fails_agent_check(self):
        from apps.core.permissions import (
            IsAgentOrAbove, IsViewerOrAbove,
        )
        req = self._make_request(self.viewer_user)
        self.assertFalse(IsAgentOrAbove().has_object_permission(req, None, self.org))
        self.assertTrue(IsViewerOrAbove().has_object_permission(req, None, self.org))
