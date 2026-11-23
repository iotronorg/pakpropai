"""
T-1032b — ADR-013 phase C, release 1 (expand) + staged release 2.

Owner membership is canonical (``Organization.owner``, ``owned_organization_ids``);
``admin_user`` is a best-effort projection; one user may own several orgs;
collaboration terms link to the membership; the legacy
AgentOrganizationMembership left the model state; the staged contract
migrations gate on drift and can drop / restore.
"""
import importlib.util
import pathlib
import re
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.agents.models import AgentOrgMembership
from apps.organizations import membership as svc
from apps.organizations.models import Organization, OrganizationMembership as M
from tests.factories import make_agent, make_developer, make_membership, make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _staged(app, name):
    path = ROOT / f'apps/{app}/contract_migrations/{name}.py'
    spec = importlib.util.spec_from_file_location(f'staged_{app}_{name}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _sql(sql, params=()):
    with connection.cursor() as cur:
        cur.execute(sql, params)


class OwnerCanonicalTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()

    def test_owner_reads_membership_not_projection(self):
        self.assertEqual(self.org.owner, self.dev)
        Organization.objects.filter(pk=self.org.pk).update(admin_user=None)   # projection lost
        self.org.refresh_from_db()
        self.assertEqual(self.org.owner, self.dev)
        self.assertEqual(svc.owned_organization_ids(self.dev), [self.org.pk])
        drift = svc.find_drift()
        self.assertIn((str(self.dev.pk), str(self.org.pk)), drift['stale_admin_projection'])
        self.assertEqual(drift['missing_owner'], [])
        call_command('reconcile_memberships', '--apply', stdout=StringIO())
        self.org.refresh_from_db()
        self.assertEqual(self.org.admin_user_id, self.dev.pk)

    def test_stale_projection_never_promotes_second_owner(self):
        other = make_user(role='developer')
        Organization.objects.filter(pk=self.org.pk).update(admin_user=other)    # bypassed hooks
        drift = svc.find_drift()
        self.assertEqual(drift['missing_owner'], [])
        svc.repair_drift()
        self.assertEqual(list(M.objects.filter(organization=self.org, role='owner', is_active=True)
                              .values_list('user_id', flat=True)), [self.dev.pk])
        self.assertEqual(Organization.objects.get(pk=self.org.pk).admin_user_id, self.dev.pk)

    def test_one_user_owns_several_orgs(self):
        second = Organization.objects.create(name='Second')
        svc.set_owner(second, self.dev)                        # admin_user slot already taken
        second.refresh_from_db()
        self.assertEqual((second.owner, second.admin_user_id), (self.dev, None))
        self.assertEqual(set(svc.owned_organization_ids(self.dev)), {self.org.pk, second.pk})
        self.assertFalse(any(svc.find_drift()[k] for k in svc.CONTRACT_BLOCKERS))
        r = _api(self.dev).get('/api/v1/organizations/')
        self.assertEqual({o['id'] for o in r.data['results']}, {str(self.org.pk), str(second.pk)})
        self.assertEqual(_api(self.dev).get(f'/api/v1/organizations/{second.pk}/').status_code, 200)
        r = _api(self.dev).get('/api/v1/organizations/me/', HTTP_X_ORGANIZATION_ID=str(second.pk))
        self.assertEqual(r.data['id'], str(second.pk))
        self.assertEqual(r.data['admin_user'], str(self.dev.pk))     # API shows the owner
        self.assertEqual(r.data['admin_phone'], self.dev.phone)

    def test_set_owner_replaces_owner_and_frees_slot(self):
        new = make_user(role='developer')
        svc.set_owner(self.org, new)
        self.org.refresh_from_db()
        self.assertEqual((self.org.owner, self.org.admin_user_id), (new, new.pk))
        self.assertFalse(M.objects.filter(user=self.dev, organization=self.org, is_active=True).exists())

    def test_platform_admin_patch_admin_user_goes_through_service(self):
        admin = make_user(role='admin')
        new = make_user(role='developer')
        r = _api(admin).patch(f'/api/v1/organizations/{self.org.pk}/', {'admin_user': str(new.pk)},
                              format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['admin_user'], str(new.pk))
        self.assertEqual(Organization.objects.get(pk=self.org.pk).owner, new)

    def test_registration_creates_owner_membership(self):
        user = make_user(role='developer')
        org = Organization.objects.create(name='Fresh')
        svc.set_owner(org, user)
        self.assertEqual(M.objects.get(user=user, organization=org).role, 'owner')
        self.assertEqual(Organization.objects.get(pk=org.pk).admin_user_id, user.pk)

    def test_notification_readers_use_owner(self):
        Organization.objects.filter(pk=self.org.pk).update(admin_user=None)
        self.org.refresh_from_db()
        self.assertEqual(self.org.owner, self.dev)            # e.g. billing / compliance alerts


class TransferMultiOrgTest(TestCase):
    def test_previous_owner_projection_moves_to_their_other_org(self):
        a, org_a = make_developer()
        b_org = Organization.objects.create(name='B')
        svc.set_owner(b_org, a)                                   # a owns A (projected) and B (not)
        heir = make_user(role='developer')
        m = make_membership(heir, org_a, role='org_admin')
        svc.transfer_ownership(a, org_a, m.pk)
        self.assertEqual(Organization.objects.get(pk=org_a.pk).admin_user_id, heir.pk)
        self.assertEqual(Organization.objects.get(pk=b_org.pk).admin_user_id, a.pk)   # slot reused
        self.assertFalse(any(svc.find_drift()[k] for k in svc.CONTRACT_BLOCKERS))


class CollaborationLinkTest(TestCase):
    def test_terms_link_membership_and_routing_respects_it(self):
        from apps.leads.models import Lead
        from apps.leads.services import suggest_agents_for_lead
        _, org = make_developer()
        user, agent = make_agent(is_active=True)
        make_membership(user, org, role='freelance_agent')
        terms = AgentOrgMembership.objects.create(agent=agent, organization=org)
        self.assertEqual(terms.membership.user_id, user.pk)
        client = make_user()
        lead = Lead.objects.create(user=client, organization=org)
        self.assertIn(agent, suggest_agents_for_lead(lead, limit=10))
        terms.membership.deactivate()                             # member removed
        self.assertNotIn(agent, suggest_agents_for_lead(lead, limit=10))

    def test_unlinked_terms_repaired(self):
        _, org = make_developer()
        user, agent = make_agent()
        terms = AgentOrgMembership.objects.create(agent=agent, organization=org)   # no membership yet
        self.assertIsNone(terms.membership_id)
        make_membership(user, org, role='freelance_agent')
        drift = svc.find_drift()
        self.assertIn((str(user.pk), str(org.pk)), drift['unlinked_collaboration'])
        svc.repair_drift(drift)
        terms.refresh_from_db()
        self.assertIsNotNone(terms.membership_id)


class StagedContractTest(TestCase):
    def test_staged_files_sit_on_latest_real_migrations(self):
        import pkgutil

        import apps.agents.migrations as agents_real
        import apps.organizations.migrations as orgs_real

        def latest(pkg):
            return max(m.name for m in pkgutil.iter_modules(pkg.__path__) if m.name[:4].isdigit())
        org_m = _staged('organizations', '0029_drop_admin_user')
        ag_m = _staged('agents', '0016_drop_agent_organization_memberships')
        self.assertIn(('organizations', latest(orgs_real)), org_m.Migration.dependencies)
        self.assertIn(('agents', latest(agents_real)), org_m.Migration.dependencies)
        self.assertEqual(ag_m.Migration.dependencies, [('agents', latest(agents_real))])

    def test_gate_refuses_on_drift_and_legacy_rows(self):
        org_m = _staged('organizations', '0029_drop_admin_user')
        ag_m = _staged('agents', '0016_drop_agent_organization_memberships')
        dev, org = make_developer()
        org_m.gate(None, None)                                     # clean → passes
        from apps.agents.models import FreelanceAgentProfile
        profile = FreelanceAgentProfile.objects.create(user=make_user(role='agent'))
        _sql("INSERT INTO agent_organization_memberships (commission_type, commission_rate, is_active, "
             "joined_at, freelance_agent_id, organization_id) VALUES ('flat', 0, true, now(), %s, %s)",
             [profile.pk, org.pk])
        with self.assertRaises(RuntimeError):
            ag_m.gate(None, None)
        _sql('DELETE FROM agent_organization_memberships')
        Organization.objects.filter(pk=org.pk).update(admin_user=None)
        with self.assertRaisesMessage(RuntimeError, 'stale_admin_projection'):
            org_m.gate(None, None)

    def test_drop_then_restore(self):
        org_m = _staged('organizations', '0029_drop_admin_user')
        ag_m = _staged('agents', '0016_drop_agent_organization_memberships')
        dev, org = make_developer()
        second = Organization.objects.create(name='Second')
        svc.set_owner(second, dev)
        _sql('SET CONSTRAINTS ALL IMMEDIATE')
        _sql(ag_m.DROP_SQL)
        self.assertEqual(svc.find_drift()['deprecated_agent_organization_memberships'], [])  # table gone
        _sql(ag_m.RESTORE_SQL)
        _sql(org_m.DROP_SQL)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'organizations' AND column_name = 'admin_user_id'")
            self.assertEqual(cur.fetchone()[0], 0)
        _sql(org_m.RESTORE_SQL)
        with connection.cursor() as cur:
            cur.execute('SELECT id, admin_user_id FROM organizations WHERE id IN (%s, %s)', [org.pk, second.pk])
            got = dict(cur.fetchall())
        self.assertEqual(got, {org.pk: dev.pk, second.pk: None})   # one projection per user


class AdminUserReadersTest(TestCase):
    """Only the projection's own modules may touch admin_user (makes T-1032b-2 mechanical)."""
    ALLOWED = {
        'apps/organizations/models.py', 'apps/organizations/membership.py',
        'apps/organizations/signals.py', 'apps/organizations/serializers.py',
        'apps/organizations/views.py', 'apps/organizations/admin.py',
        'apps/organizations/management/commands/reconcile_memberships.py',
        'apps/core/permissions.py',            # docstring only
    }

    def test_no_new_readers(self):
        pattern = re.compile(r'\badmin_user\b|owned_organization\b')
        offenders = []
        for path in (ROOT / 'apps').rglob('*.py'):
            rel = path.relative_to(ROOT).as_posix()
            if '/migrations/' in rel or '/contract_migrations/' in rel or '/tests' in rel or rel in self.ALLOWED:
                continue
            if pattern.search(path.read_text(encoding='utf-8')):
                offenders.append(rel)
        self.assertEqual(offenders, [])
