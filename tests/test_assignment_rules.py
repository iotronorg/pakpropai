"""T-3040 — team management and lead assignment rules (FR-CRM-007, ADR-063)."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentOrgMembership
from apps.conversations.models import Conversation
from apps.core.models import AuditEvent
from apps.enquiries.models import Enquiry
from apps.leads import assignment as A
from apps.leads.models import AgentRouting, AssignmentRules, Lead, LeadActivity
from apps.organizations.models import OrganizationMembership
from tests.factories import (make_agent, make_client, make_developer, make_lead, make_membership, make_org,
                             make_property, make_user)
from tests.test_runtime import LOCMEM

BASE = '/api/v1/leads/'
R = Lead.RoutingState
Role = OrganizationMembership.Role


def api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def agent(org, **kw):
    kw.setdefault('is_active', True)
    kw.setdefault('cities', ['Lahore'])
    return make_agent(org=org, **kw)[1]


def rules(org, **kw):
    return AssignmentRules.objects.update_or_create(organization=org, defaults=kw)[0]


@override_settings(CACHES=LOCMEM)
class RankingTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))

    def lead(self, **kw):
        kw.setdefault('city_interest', 'Lahore')
        return make_lead(make_client(), self.org, **kw)

    def ids(self, lead):
        return [c.agent.pk for c in A.rank(lead)]

    def test_defaults_keep_the_old_matching(self):
        a = agent(self.org, rating=5)
        b = agent(self.org, rating=1)
        karachi = agent(self.org, cities=['Karachi'])
        agent(make_org(name='Other'))                              # another organization's agent
        self.assertEqual(self.ids(self.lead()), [a.pk, b.pk])
        self.assertNotIn(karachi.pk, self.ids(self.lead()))
        self.assertFalse(AssignmentRules.objects.exists())

    def test_round_robin_rotates_and_skips_paused(self):
        rules(self.org, strategy='round_robin')
        a, b, c = agent(self.org), agent(self.org), agent(self.org)
        got = [A.auto_assign(self.lead()).pk for _ in range(4)]
        self.assertEqual(got, [a.pk, b.pk, c.pk, a.pk])
        AgentRouting.objects.create(organization=self.org, agent=b, accepts_leads=False)
        self.assertEqual([A.auto_assign(self.lead()).pk for _ in range(2)], [c.pk, a.pk])

    def test_least_loaded_and_caps(self):
        rules(self.org, strategy='least_loaded', max_open_leads=2)
        a, b = agent(self.org, rating=5), agent(self.org)
        self.lead(assigned_agent=a)
        self.lead(assigned_agent=b, routing_state=R.CLOSED)        # closed leads are not load
        self.assertEqual(self.ids(self.lead()), [b.pk, a.pk])
        self.lead(assigned_agent=a)
        self.assertEqual(self.ids(self.lead()), [b.pk])             # a is at the cap of 2
        AgentRouting.objects.create(organization=self.org, agent=a, max_open_leads=5)
        self.assertEqual(self.ids(self.lead()), [b.pk, a.pk])       # own cap overrides
        self.lead(assigned_agent=b)
        self.lead(assigned_agent=b)
        queued = self.lead()
        AgentRouting.objects.filter(agent=a).update(max_open_leads=2)
        self.assertIsNone(A.auto_assign(queued))                    # everyone full: stays queued
        queued.refresh_from_db()
        self.assertIsNone(queued.assigned_agent_id)

    def test_only_available(self):
        busy = agent(self.org, availability_status='busy', rating=5)
        free = agent(self.org)
        self.assertEqual(self.ids(self.lead()), [busy.pk, free.pk])
        rules(self.org, only_available=True)
        self.assertEqual(self.ids(self.lead()), [free.pk])

    def test_language_require_and_prefer(self):
        urdu = agent(self.org, languages=['Urdu', 'English'])
        english = agent(self.org, languages=['English'], rating=5)
        lead = self.lead()
        Conversation.objects.create(key='k1', channel='web', user=lead.user, organization=self.org,
                                    language='roman', last_message_at=timezone.now())
        rules(self.org, match_language='require')
        self.assertEqual(self.ids(lead), [urdu.pk])
        self.assertEqual(A.rank(lead)[0].matched, ['city', 'language'])
        Agent.objects.filter(pk=urdu.pk).update(languages=['Punjabi'])
        self.assertEqual(self.ids(lead), [])
        rules(self.org, match_language='prefer')
        self.assertEqual(self.ids(lead), [english.pk, urdu.pk])
        self.assertEqual(self.ids(self.lead()), [english.pk, urdu.pk])   # no conversation: rule skipped

    def test_area_from_enquired_listing(self):
        dha = agent(self.org, areas=['DHA Phase 5'])
        other = agent(self.org, areas=['Gulberg III'], rating=5)
        lead = self.lead()
        listing = make_property(org=self.org, location='DHA Phase 5, Block C')
        Enquiry.objects.create(user=lead.user, kind='organization', organization=self.org, listing=listing,
                               subject='x', lead=lead, consent_version='v1', consent_via='api',
                               consented_at=timezone.now(), last_message_at=timezone.now())
        rules(self.org, match_area='prefer')
        self.assertEqual(self.ids(lead), [dha.pk])
        self.assertEqual(self.ids(self.lead()), [other.pk, dha.pk])

    def test_city_off_and_specialization_require(self):
        karachi = agent(self.org, cities=['Karachi'], specializations=['residential_rent'])
        lahore = agent(self.org, specializations=['plots'])
        rules(self.org, match_city='off', match_specialization='require')
        self.assertEqual(self.ids(self.lead(intent='rent')), [karachi.pk])
        self.assertEqual(self.ids(self.lead(intent='buy')), [lahore.pk])

    def test_freelance_collaborators(self):
        free = make_agent(is_active=True, cities=['Lahore'])[1]          # no organization of its own
        term = AgentOrgMembership.objects.create(agent=free, organization=self.org)
        self.assertEqual(self.ids(self.lead()), [free.pk])
        term.status = 'ended'
        term.save()
        self.assertEqual(self.ids(self.lead()), [])

    def test_assign_records_routing_rule_and_counter(self):
        rules(self.org, strategy='least_loaded')
        a = agent(self.org)
        lead = self.lead(routing_state=R.ORG_QUEUE)
        self.assertEqual(A.auto_assign(lead), a)
        lead.refresh_from_db()
        self.assertEqual((lead.assigned_agent_id, lead.routing_state), (a.pk, R.AGENT_ASSIGNED))
        act = LeadActivity.objects.get(lead=lead, action='assigned')
        self.assertEqual(act.meta['rule']['strategy'], 'least_loaded')
        self.assertTrue(act.meta['auto'])
        self.assertEqual(Agent.objects.get(pk=a.pk).total_leads, 1)
        self.assertIsNone(A.auto_assign(lead))                       # already assigned


@override_settings(CACHES=LOCMEM)
class ApiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.a = agent(self.org, languages=['Urdu'])
        self.b = agent(self.org)

    def staff(self, role):
        user = make_user(role='developer')
        make_membership(user, self.org, role=role)
        return user

    def test_rules_read_and_change(self):
        r = api(self.owner).get(BASE + 'assignment/rules/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data['strategy'], r.data['match_city'], r.data['saved']), ('best_match', 'require', False))
        r = api(self.owner).patch(BASE + 'assignment/rules/', {'strategy': 'round_robin', 'max_open_leads': 10,
                                                               'match_language': 'prefer'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data['strategy'], r.data['max_open_leads'], r.data['saved']), ('round_robin', 10, True))
        ev = AuditEvent.objects.get(action='assignment.rules_changed', organization_id=str(self.org.pk))
        self.assertEqual(ev.meta['changed']['strategy'], ['best_match', 'round_robin'])
        for bad in ({'strategy': 'random'}, {'max_open_leads': 0}, {'only_available': 'yes'}, {'colour': 'red'}):
            self.assertEqual(api(self.owner).patch(BASE + 'assignment/rules/', bad, format='json').status_code, 400)

    def test_who_may_read_and_change(self):
        manager, operator = self.staff(Role.TEAM_MANAGER), self.staff(Role.CRM_OPERATOR)
        self.assertEqual(api(manager).patch(BASE + 'assignment/rules/', {'strategy': 'least_loaded'},
                                            format='json').status_code, 200)
        self.assertEqual(api(operator).get(BASE + 'assignment/rules/').status_code, 200)
        self.assertEqual(api(operator).patch(BASE + 'assignment/rules/', {'strategy': 'best_match'},
                                             format='json').status_code, 403)
        self.assertEqual(api(self.a.user).get(BASE + 'assignment/team/').status_code, 403)
        self.assertEqual(api(make_developer()[0]).get(BASE + 'assignment/team/').json()['results'], [])
        admin = make_user(role='admin')
        self.assertEqual(api(admin).get(BASE + 'assignment/rules/').status_code, 400)          # names the org
        self.assertEqual(api(admin).get(BASE + f'assignment/rules/?organization={self.org.pk}').status_code, 200)

    def test_team_list_and_pause(self):
        lead = make_lead(make_client(), self.org, assigned_agent=self.a)
        r = api(self.owner).get(BASE + 'assignment/team/')
        rows = {x['agent_id']: x for x in r.data['results']}
        self.assertEqual(rows[str(self.a.pk)]['open_leads'], 1)
        self.assertTrue(rows[str(self.a.pk)]['receives_leads'])
        self.assertNotIn('phone', rows[str(self.a.pk)])
        r = api(self.owner).patch(BASE + f'assignment/team/{self.a.pk}/', {'accepts_leads': False,
                                                                          'max_open_leads': 3}, format='json')
        self.assertEqual((r.status_code, r.data['accepts_leads'], r.data['receives_leads'], r.data['cap']),
                         (200, False, False, 3))
        self.assertTrue(AuditEvent.objects.filter(action='assignment.agent_routing_changed').exists())
        self.assertEqual([c.agent.pk for c in A.rank(lead)], [self.b.pk])
        foreign = agent(make_org(name='Other'))
        self.assertEqual(api(self.owner).patch(BASE + f'assignment/team/{foreign.pk}/', {'accepts_leads': False},
                                               format='json').status_code, 404)

    def test_suggest_explains_and_auto_assign_follows_rules(self):
        rules(self.org, match_language='require')
        lead = make_lead(make_client(), self.org, city_interest='Lahore', assigned_agent=self.b)
        Conversation.objects.create(key='k2', channel='whatsapp', user=lead.user, organization=self.org,
                                    language='ur', last_message_at=timezone.now())
        r = api(self.owner).get(BASE + f'{lead.pk}/suggest-agents/')
        self.assertEqual([x['id'] for x in r.data], [self.a.pk])
        self.assertEqual(r.data[0]['assignment']['matched'], ['city', 'language'])
        r = api(self.owner).post(BASE + f'{lead.pk}/auto-assign/')
        self.assertEqual((r.status_code, str(r.data['assigned_agent_id'])), (200, str(self.a.pk)))

    def test_manual_and_bulk_assign_stay_in_the_organization(self):
        other_org = make_org(name='Other')
        foreign = agent(other_org)
        lead = make_lead(make_client(), self.org)
        self.assertEqual(api(self.owner).post(BASE + f'{lead.pk}/assign/', {'agent_id': foreign.pk}).status_code, 404)
        free = make_agent(is_active=True)[1]
        AgentOrgMembership.objects.create(agent=free, organization=self.org)
        self.assertEqual(api(self.owner).post(BASE + f'{lead.pk}/assign/', {'agent_id': free.pk}).status_code, 200)

        leads = [make_lead(make_client(), self.org) for _ in range(3)]
        ids = [str(x.pk) for x in leads]
        r = api(self.owner).post(BASE + 'bulk-assign/', {'lead_ids': ids, 'agent_id': foreign.pk}, format='json')
        self.assertEqual(r.status_code, 404)                        # was: any active agent
        r = api(self.owner).post(BASE + 'bulk-assign/', {'lead_ids': ids, 'agent_id': self.b.pk}, format='json')
        self.assertEqual((r.status_code, r.data['assigned']), (200, 3))
        self.assertEqual(set(Lead.objects.filter(pk__in=ids).values_list('routing_state', flat=True)),
                         {R.AGENT_ASSIGNED})
        self.assertEqual(LeadActivity.objects.filter(lead__in=leads, action='assigned').count(), 3)
        r = api(self.owner).post(BASE + 'bulk-assign/', {'lead_ids': ['x'] * 201, 'agent_id': self.b.pk},
                                 format='json')
        self.assertEqual(r.status_code, 400)
        admin, fresh = make_user(role='admin'), [str(make_lead(make_client(), self.org).pk) for _ in range(2)]
        r = api(admin).post(BASE + 'bulk-assign/', {'lead_ids': fresh, 'agent_id': foreign.pk}, format='json')
        self.assertEqual((r.status_code, r.data['assigned']), (200, 0))   # another organization's agent
        r = api(admin).post(BASE + 'bulk-assign/', {'lead_ids': fresh, 'agent_id': self.a.pk}, format='json')
        self.assertEqual((r.status_code, r.data['assigned']), (200, 2))
