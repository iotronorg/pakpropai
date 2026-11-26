"""T-3030 — configurable pipeline stages per organization (FR-CRM-002, ADR-062)."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.leads import pipeline as P
from apps.leads.models import Lead, LeadActivity, PipelineStage
from apps.organizations.models import OrganizationMembership
from tests.factories import make_agent, make_client, make_developer, make_lead, make_membership, make_org, make_user
from tests.test_runtime import LOCMEM

BASE = '/api/v1/leads/pipeline/'
R = Lead.RoutingState


def api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@override_settings(CACHES=LOCMEM)
class ServiceTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))

    def keys(self):
        return [s.key for s in P.stages(self.org)]

    def test_defaults_on_first_use_and_unstaged_leads_are_first(self):
        self.assertEqual(self.keys(), ['new', 'contacted', 'qualified', 'viewing', 'offer', 'won', 'lost'])
        self.assertEqual(PipelineStage.objects.filter(organization=self.org).count(), 7)
        P.stages(self.org)
        self.assertEqual(PipelineStage.objects.count(), 7)
        lead = make_lead(make_client(), self.org)
        self.assertEqual(P.current(lead).key, 'new')
        self.assertIsNone(P.current(make_lead(make_client())))

    def test_move_closes_and_reopens_routing(self):
        _, agent = make_agent(org=self.org)
        lead = make_lead(make_client(), self.org, routing_state=R.AGENT_ASSIGNED, assigned_agent=agent)
        stages = {s.key: s for s in P.stages(self.org)}
        lead = P.move(lead, stages['won'].pk, user=self.owner, note='signed')
        self.assertEqual((lead.stage.key, lead.routing_state), ('won', R.CLOSED))
        self.assertIsNotNone(lead.stage_changed_at)
        a = LeadActivity.objects.get(lead=lead, action='stage')
        self.assertEqual((a.meta['from'], a.meta['to'], a.meta['routing'], a.notes),
                         ('new', 'won', [R.AGENT_ASSIGNED, R.CLOSED], 'signed'))
        lead = P.move(lead, stages['offer'].pk, user=self.owner)
        self.assertEqual(lead.routing_state, R.AGENT_ASSIGNED)
        self.assertEqual(lead.status, Lead.Status.NEW)            # the AI's qualification is untouched
        other = P.stages(make_org(name='Other'))[1]
        with self.assertRaises(P.PipelineError):
            P.move(lead, other.pk)

    def test_add_rename_reorder(self):
        new = P.stages(self.org)[0]
        s = P.add(self.org, name='Site  visit', after=P.stages(self.org)[2].pk, user=self.owner)
        self.assertEqual((s.key, s.name, self.keys()[3]), ('site-visit', 'Site visit', 'site-visit'))
        self.assertEqual(P.add(self.org, name='Site-visit!').key, 'site-visit-2')
        with self.assertRaises(P.PipelineError):
            P.add(self.org, name='site visit')
        P.update(s, name='Booked visit')
        self.assertEqual(PipelineStage.objects.get(pk=s.pk).key, 'site-visit')     # keys stay
        lead = make_lead(make_client(), self.org)
        ids = [x.pk for x in P.stages(self.org)]
        ids[0], ids[1] = ids[1], ids[0]
        P.reorder(self.org, ids)
        self.assertEqual(self.keys()[:2], ['contacted', 'new'])
        lead.refresh_from_db()
        self.assertEqual(lead.stage_id, new.pk)                   # did not slide into the new first stage
        with self.assertRaises(P.PipelineError):
            P.reorder(self.org, ids[:-1])
        self.assertTrue(AuditEvent.objects.filter(action='pipeline.stage_added', organization_id=str(self.org.pk))
                        .exists())

    def test_archive_moves_leads_and_keeps_one_open_stage(self):
        stages = {s.key: s for s in P.stages(self.org)}
        a, b = make_lead(make_client(), self.org), make_lead(make_client(), self.org)
        P.move(b, stages['viewing'].pk)
        with self.assertRaises(P.PipelineError) as e:
            P.archive(stages['new'], move_to=None)
        self.assertEqual(e.exception.field, 'move_to')
        self.assertEqual(P.archive(stages['new'], move_to=stages['contacted'].pk), 1)
        a.refresh_from_db()
        self.assertEqual(a.stage.key, 'contacted')
        self.assertNotIn('new', self.keys())
        self.assertEqual(P.archive(stages['offer'], move_to=None), 0)
        for key in ('contacted', 'qualified'):
            P.archive(stages[key], move_to=stages['viewing'].pk)
        with self.assertRaises(P.PipelineError) as e:
            P.archive(stages['viewing'], move_to=stages['won'].pk)
        self.assertEqual(e.exception.code, 'pipeline.last_open')
        with self.assertRaises(P.PipelineError):
            P.update(stages['viewing'], kind='won')
        self.assertEqual(P.update(stages['lost'], kind='open').kind, 'open')

    def test_kind_change_refused_with_leads(self):
        stages = {s.key: s for s in P.stages(self.org)}
        make_lead(make_client(), self.org)                        # unstaged = in "new"
        with self.assertRaises(P.PipelineError) as e:
            P.update(stages['new'], kind='lost')
        self.assertEqual(e.exception.code, 'pipeline.not_empty')

    def test_stage_from_another_org_counts_as_first(self):
        lead = make_lead(make_client(), self.org)
        P.move(lead, P.stages(self.org)[3].pk)
        other = make_org(name='Other')
        Lead.objects.filter(pk=lead.pk).update(organization=other)
        lead.refresh_from_db()
        self.assertEqual(P.current(lead).organization_id, other.pk)
        self.assertEqual(P.counts(Lead.objects.all(), other)[P.first_stage(other).pk], 1)


@override_settings(CACHES=LOCMEM)
class ApiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.agent_user, self.agent = make_agent(org=self.org)
        self.mine = make_lead(make_client(), self.org, assigned_agent=self.agent, routing_state=R.AGENT_ASSIGNED,
                              priority=5)
        self.other = make_lead(make_client(), self.org)
        self.rival_owner, self.rival = make_developer(org=make_org(name='Rival'))
        make_lead(make_client(), self.rival)

    def stages(self, user=None):
        return api(user or self.owner).get(BASE + 'stages/').data['results']

    def test_list_board_and_lead_field(self):
        rows = self.stages()
        self.assertEqual((rows[0]['key'], rows[0]['count'], len(rows)), ('new', 2, 7))
        board = api(self.owner).get(BASE + 'board/', {'per_stage': 1}).data['stages']
        self.assertEqual((board[0]['count'], len(board[0]['leads']), board[0]['leads'][0]['id']),
                         (2, 1, str(self.mine.pk)))
        self.assertNotIn('phone', board[0]['leads'][0])
        agent_board = api(self.agent_user).get(BASE + 'board/').data['stages']
        self.assertEqual([lead['id'] for lead in agent_board[0]['leads']], [str(self.mine.pk)])
        lead = api(self.owner).get(f'/api/v1/leads/{self.mine.pk}/').data
        self.assertEqual(lead['stage']['key'], 'new')
        listed = api(self.owner).get('/api/v1/leads/').data
        rows = listed['results'] if isinstance(listed, dict) else listed
        self.assertTrue(all(r['stage']['key'] == 'new' for r in rows))

    def test_owner_manages_stages(self):
        c = api(self.owner)
        r = c.post(BASE + 'stages/', {'name': 'Negotiation', 'after': self.stages()[4]['id']}, format='json')
        self.assertEqual((r.status_code, r.data['position']), (201, 5))
        self.assertEqual(c.patch(f"{BASE}stages/{r.data['id']}/", {'name': 'Negotiating'}, format='json')
                         .data['name'], 'Negotiating')
        ids = [s['id'] for s in self.stages()][::-1]
        self.assertEqual(c.post(BASE + 'stages/reorder/', {'order': ids}, format='json').status_code, 200)
        self.assertEqual(self.stages()[0]['key'], 'lost')
        first_open = next(s for s in self.stages() if s['kind'] == 'open')
        r = c.post(f"{BASE}stages/{self.stages()[1]['id']}/archive/", {'move_to': first_open['id']}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.post(BASE + 'stages/', {'name': ''}, format='json').status_code, 400)
        rival_stage = api(self.rival_owner).get(BASE + 'stages/').data['results'][0]['id']
        self.assertEqual(c.patch(f'{BASE}stages/{rival_stage}/', {'name': 'x'}, format='json').status_code, 404)

    def test_members_cannot_change_stages(self):
        manager = make_user(role='developer')
        make_membership(manager, self.org, role=OrganizationMembership.Role.SALES_MANAGER)
        for user in (self.agent_user, manager):
            self.assertEqual(api(user).post(BASE + 'stages/', {'name': 'X'}, format='json').status_code, 403)
            self.assertEqual(len(self.stages(user)), 7)
        self.assertEqual(api(make_client()).get(BASE + 'stages/').status_code, 403)

    def test_move_stage_permissions(self):
        stages = {s['key']: s['id'] for s in self.stages()}
        r = api(self.agent_user).post(f'/api/v1/leads/{self.mine.pk}/move-stage/', {'stage': stages['viewing']},
                                      format='json')
        self.assertEqual((r.status_code, r.data['stage']['key']), (200, 'viewing'))
        r = api(self.owner).post(f'/api/v1/leads/{self.mine.pk}/move-stage/', {'stage': stages['lost']}, format='json')
        self.assertEqual((r.data['stage']['key'], r.data['routing_state']), ('lost', R.CLOSED))
        self.assertEqual(api(self.owner).post(f'/api/v1/leads/{self.mine.pk}/move-stage/', {}, format='json')
                         .status_code, 400)
        rival_stage = api(self.rival_owner).get(BASE + 'stages/').data['results'][1]['id']
        self.assertEqual(api(self.owner).post(f'/api/v1/leads/{self.other.pk}/move-stage/', {'stage': rival_stage},
                                              format='json').status_code, 400)
        self.assertEqual(api(self.rival_owner).post(f'/api/v1/leads/{self.other.pk}/move-stage/',
                                                    {'stage': rival_stage}, format='json').status_code, 404)
        viewer = make_user(role='developer')
        make_membership(viewer, self.org, role=OrganizationMembership.Role.VIEWER)
        self.assertEqual(api(viewer).post(f'/api/v1/leads/{self.other.pk}/move-stage/', {'stage': stages['offer']},
                                          format='json').status_code, 403)

    def test_platform_admin_names_the_organization(self):
        admin = make_user(role='admin')
        self.assertEqual(api(admin).get(BASE + 'stages/').status_code, 400)
        r = api(admin).get(BASE + 'stages/', {'organization': str(self.org.pk)})
        self.assertEqual((r.status_code, r.data['results'][0]['count']), (200, 2))
        self.assertEqual(api(admin).post(BASE + 'stages/', {'name': 'Hold', 'organization': str(self.org.pk)},
                                         format='json').status_code, 201)
