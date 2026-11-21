"""
T-1030 / ADR-013: OrganizationMembership is canonical; Agent.organization and
Organization.admin_user are projections kept in sync by a single write path.
"""
from io import StringIO

from django.apps import apps as django_apps
from django.core.management import CommandError, call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.agents.models import Agent
from apps.core.permissions import get_user_org
from apps.organizations.membership import find_drift, primary_membership
from apps.organizations.models import Organization, OrganizationMembership as M
from tests.factories import make_agent, make_developer, make_org, make_user


def _active(user, org):
    return M.objects.filter(user=user, organization=org, is_active=True).first()


class AgentProjectionSyncTest(TestCase):
    def test_agent_created_with_org_gets_membership(self):
        org = make_org()
        user = make_user(role='agent')
        Agent.objects.create(user=user, organization=org, name='A', phone=user.phone,
                             employment_type='internal')
        m = _active(user, org)
        self.assertIsNotNone(m)
        self.assertEqual((m.role, m.employment_type), ('agent', 'internal'))
        self.assertEqual(get_user_org(user), org)

    def test_freelance_agent_with_org_gets_freelance_role(self):
        org = make_org()
        user = make_user(role='agent')
        Agent.objects.create(user=user, organization=org, name='A', phone=user.phone,
                             employment_type='freelance')
        self.assertEqual(_active(user, org).role, 'freelance_agent')

    def test_moving_agent_deactivates_old_membership(self):
        org_a, org_b = make_org('A'), make_org('B')
        user, agent = make_agent(org=org_a)
        agent.organization = org_b
        agent.save()
        self.assertIsNone(_active(user, org_a))
        self.assertIsNotNone(_active(user, org_b))
        self.assertEqual(get_user_org(user), org_b)

    def test_unlinking_user_deactivates_membership(self):
        org = make_org()
        user, agent = make_agent(org=org)
        agent.user = None
        agent.save()
        self.assertIsNone(_active(user, org))

    def test_linking_user_later_creates_membership(self):
        org = make_org()
        agent = Agent.objects.create(name='A', phone='+923001230000', organization=org,
                                     employment_type='internal')
        user = make_user(role='agent')
        agent.user = user
        agent.save(update_fields=['user'])
        self.assertIsNotNone(_active(user, org))

    def test_rejoining_reactivates_membership(self):
        org = make_org()
        user, agent = make_agent(org=org)
        agent.organization, agent.employment_type = None, 'freelance'
        agent.save()
        agent.organization, agent.employment_type = org, 'internal'
        agent.save()
        self.assertEqual(M.objects.filter(user=user, organization=org).count(), 1)
        self.assertIsNotNone(_active(user, org))

    def test_owner_membership_survives_agent_move(self):
        dev, org = make_developer()
        Agent.objects.create(user=dev, organization=org, name='D', phone=dev.phone,
                             employment_type='internal')
        agent = dev.agent_profile
        agent.organization, agent.employment_type = None, 'freelance'
        agent.save()
        self.assertEqual(_active(dev, org).role, 'owner')

    def test_unrelated_save_does_not_touch_memberships(self):
        org = make_org()
        user, agent = make_agent(org=org)
        m = _active(user, org)
        m.role = 'team_manager'
        m.save(update_fields=['role'])
        agent.name = 'Renamed'
        agent.save(update_fields=['name'])
        agent.refresh_from_db()
        agent.bio = 'x'
        agent.save()
        self.assertEqual(_active(user, org).role, 'team_manager')


class OwnerProjectionSyncTest(TestCase):
    def test_org_created_with_admin_gets_owner_membership(self):
        dev = make_user(role='developer')
        org = Organization.objects.create(name='X', admin_user=dev)
        self.assertEqual(_active(dev, org).role, 'owner')
        self.assertEqual(get_user_org(dev), org)

    def test_admin_transfer_moves_ownership(self):
        old, new = make_user(role='developer'), make_user(role='developer')
        org = Organization.objects.create(name='X', admin_user=old)
        org.admin_user = new
        org.save()
        self.assertIsNone(_active(old, org))
        self.assertEqual(_active(new, org).role, 'owner')

    def test_existing_member_promoted_to_owner(self):
        org = make_org()
        user = make_user(role='developer')
        M.objects.create(user=user, organization=org, role='org_admin')
        org.admin_user = user
        org.save(update_fields=['admin_user'])
        self.assertEqual(_active(user, org).role, 'owner')

    def test_admin_api_create_org_resolves_for_developer(self):
        admin = make_user(role='admin')
        dev = make_user(role='developer')
        client = APIClient()
        client.force_authenticate(admin)
        r = client.post('/api/v1/organizations/', {'name': 'Admin Made', 'admin_user': dev.id},
                        format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(str(get_user_org(dev).id), str(r.data['id']))


class WriterPathTest(TestCase):
    """The API paths that previously left the canonical table stale."""

    def setUp(self):
        self.dev, self.org = make_developer()
        self.client = APIClient()
        self.client.force_authenticate(self.dev)

    def test_team_remove_revokes_org_access(self):
        user, agent = make_agent(org=self.org)
        self.assertEqual(get_user_org(user), self.org)
        r = self.client.delete(f'/api/v1/agents/team/{agent.id}/')
        self.assertEqual(r.status_code, 204)
        self.assertIsNone(_active(user, self.org))
        self.assertIsNone(get_user_org(user))

    def test_team_add_grants_membership(self):
        user, agent = make_agent()
        r = self.client.post('/api/v1/agents/team/', {'agent_id': agent.id}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(get_user_org(user), self.org)

    def test_member_api_delete_clears_agent_projection(self):
        user, agent = make_agent(org=self.org)
        m = _active(user, self.org)
        r = self.client.delete(f'/api/v1/organizations/me/members/{m.id}/')
        self.assertEqual(r.status_code, 204)
        agent.refresh_from_db()
        self.assertIsNone(agent.organization_id)
        self.assertEqual(agent.employment_type, 'freelance')
        self.assertIsNone(_active(user, self.org))

    def test_agent_registration_with_org_creates_membership(self):
        anon = APIClient()
        r = anon.post('/api/v1/agents/register/', {
            'name': 'New Agent', 'phone': '+923009998887', 'password': 'longpassword1',
            'agent_type': 'individual', 'organization': str(self.org.id),
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        agent = Agent.objects.get(phone='+923009998887')
        self.assertIsNotNone(_active(agent.user, self.org))


    def test_member_invite_by_phone_works_with_json(self):
        # KI-032: PIIMaskingMiddleware used to rewrite the phone in JSON bodies.
        invited = make_user(phone='+923001112233', role='agent')
        r = self.client.post('/api/v1/organizations/me/members/',
                             {'phone': '+923001112233', 'role': 'viewer'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(_active(invited, self.org).role, 'viewer')


class PrimaryMembershipTest(TestCase):
    def test_agent_org_preferred_over_earlier_invite(self):
        org_a, org_b = make_org('A'), make_org('B')
        user = make_user(role='agent')
        M.objects.create(user=user, organization=org_a, role='viewer')
        Agent.objects.create(user=user, organization=org_b, name='A', phone=user.phone,
                             employment_type='internal')
        self.assertEqual(primary_membership(user).organization, org_b)

    def test_owned_org_preferred(self):
        dev, org = make_developer()
        other = make_org('Other')
        M.objects.filter(user=dev).update(joined_at='2030-01-01T00:00:00Z')
        M.objects.create(user=dev, organization=other, role='viewer')
        self.assertEqual(primary_membership(dev).organization, org)

    def test_no_membership(self):
        self.assertIsNone(primary_membership(make_user()))


class DriftAndBackfillTest(TestCase):
    def _make_drift(self):
        """Simulate pre-ADR-013 writes that bypassed the canonical table."""
        dev, org = make_developer()
        user, agent = make_agent()
        Agent.objects.filter(pk=agent.pk).update(organization=org, employment_type='internal')
        M.objects.filter(user=dev, organization=org).delete()
        return dev, user, org

    def test_find_drift_reports_missing_rows(self):
        dev, user, org = self._make_drift()
        drift = find_drift()
        self.assertIn((str(dev.id), str(org.id)), drift['missing_owner'])
        self.assertIn((str(user.id), str(org.id)), drift['missing_agent'])

    def test_command_reports_then_repairs(self):
        dev, user, org = self._make_drift()
        out = StringIO()
        call_command('reconcile_memberships', stdout=out)
        self.assertIn('missing_owner (repairable): 1', out.getvalue())
        self.assertIsNone(_active(dev, org))  # report only by default

        call_command('reconcile_memberships', '--apply', stdout=StringIO())
        self.assertEqual(_active(dev, org).role, 'owner')
        self.assertIsNotNone(_active(user, org))
        drift = find_drift()
        self.assertEqual((drift['missing_owner'], drift['missing_agent']), ([], []))

    def test_fail_on_drift(self):
        self._make_drift()
        with self.assertRaises(CommandError):
            call_command('reconcile_memberships', '--fail-on-drift', stdout=StringIO())

    def test_report_only_categories(self):
        org = make_org()
        stray = make_user(role='agent')
        M.objects.create(user=stray, organization=org, role='agent')
        extra = make_user(role='developer')
        M.objects.create(user=extra, organization=org, role='owner')
        drift = find_drift()
        self.assertIn((str(stray.id), str(org.id)), drift['orphan_agent'])
        self.assertIn((str(extra.id), str(org.id)), drift['extra_owner'])
        call_command('reconcile_memberships', '--apply', stdout=StringIO())
        self.assertIsNotNone(_active(stray, org))  # never auto-revoked

    def test_backfill_migration_is_create_only_and_idempotent(self):
        import importlib
        mig = importlib.import_module(
            'apps.organizations.migrations.0024_backfill_missing_memberships')
        dev, user, org = self._make_drift()
        inactive = make_user(role='agent')
        M.objects.create(user=inactive, organization=org, role='agent', is_active=False)
        Agent.objects.create(user=inactive, name='I', phone=inactive.phone)
        Agent.objects.filter(user=inactive).update(organization=org, employment_type='internal')

        mig.backfill(django_apps, None)
        mig.backfill(django_apps, None)

        self.assertEqual(_active(dev, org).role, 'owner')
        self.assertEqual(_active(user, org).role, 'agent')
        self.assertEqual(M.objects.filter(user=inactive, organization=org).count(), 1)
        self.assertIsNone(_active(inactive, org))  # existing inactive row untouched
