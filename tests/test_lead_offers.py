"""T-4060 — offers register on leads (FR-CRM-009, ADR-068)."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.leads import offers as OF
from apps.leads import pipeline as PL
from apps.leads.models import Lead, LeadActivity, LeadOffer
from apps.leads.tasks import expire_lead_offers
from tests.factories import make_agent, make_client, make_developer, make_org, make_property
from tests.test_runtime import LOCMEM

URL = '/api/v1/leads/offers/'


@override_settings(CACHES=LOCMEM)
class OfferTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.ua, self.a = make_agent(org=self.org, name='Agent A', is_active=True)
        self.ub, self.b = make_agent(org=self.org, name='Agent B', is_active=True)
        self.lead = Lead.objects.create(user=make_client(), organization=self.org, assigned_agent=self.a)
        self.other_lead = Lead.objects.create(user=make_client(), organization=self.org, assigned_agent=self.b)
        self.p = make_property(org=self.org, title='Villa', price=30_000_000)

    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def offer(self, user=None, **body):
        data = {'lead': str(self.lead.pk), 'property': str(self.p.pk), 'amount': 28_000_000, 'currency': 'pkr',
                **body}
        return self.api(user or self.ua).post(URL, data, format='json')

    def test_record_counter_accept_and_negotiation(self):
        r = self.offer(conditions='Subject to bank loan')
        self.assertEqual(r.status_code, 201, r.data)
        first = r.data['id']
        self.assertEqual((r.data['status'], r.data['currency'], r.data['party']), ('open', 'PKR', 'client'))
        r = self.offer(previous=first, party='counterparty', amount=29_500_000)
        self.assertEqual(r.status_code, 201, r.data)
        counter = r.data['id']
        self.assertEqual(LeadOffer.objects.get(pk=first).status, 'countered')
        self.assertEqual(self.offer(previous=first, party='client', amount=29_000_000).status_code, 409)  # not open
        self.assertEqual(self.offer(previous=counter, party='counterparty').status_code, 400)        # same side
        r = self.api(self.ua).post(URL + f'{counter}/decide/', {'status': 'accepted', 'note': 'Agreed'}, format='json')
        self.assertEqual((r.status_code, r.data['status']), (200, 'accepted'))
        self.assertEqual(self.api(self.ua).post(URL + f'{counter}/decide/', {'status': 'rejected'},
                                                format='json').status_code, 409)
        r = self.api(self.owner).get(URL + f'{counter}/')
        self.assertEqual([o['amount'] for o in r.data['negotiation']], [28_000_000, 29_500_000])
        self.assertEqual(LeadActivity.objects.filter(lead=self.lead, action='offer').count(), 3)
        self.assertTrue(AuditEvent.objects.filter(action='lead.offer_accepted').exists())
        # one accepted offer per lead and listing
        again = self.offer(amount=30_000_000).data['id']
        r = self.api(self.ua).post(URL + f'{again}/decide/', {'status': 'accepted'}, format='json')
        self.assertEqual((r.status_code, r.data['code']), (409, 'offer.already_accepted'))
        self.assertEqual(LeadOffer.objects.get(pk=again).status, 'open')
        self.assertEqual(self.api(self.owner).get(URL + 'summary/', {'lead': str(self.lead.pk)}).data,
                         {'open': 1, 'countered': 1, 'accepted': 1, 'rejected': 0, 'withdrawn': 0, 'expired': 0})

    def test_who_may_see_and_record(self):
        self.assertEqual(self.offer(user=self.ub).status_code, 404)                 # not B's lead
        self.assertEqual(self.offer().status_code, 201)
        self.assertEqual(len(self.api(self.ub).get(URL).data['results']), 0)
        self.assertEqual(len(self.api(self.owner).get(URL).data['results']), 1)
        outsider, _ = make_developer(org=make_org(name='Other'))
        self.assertEqual(len(self.api(outsider).get(URL).data['results']), 0)
        self.assertEqual(self.offer(user=outsider).status_code, 404)
        self.assertEqual(self.api(make_client()).get(URL).data['results'], [])
        offer = LeadOffer.objects.get()
        self.assertEqual(self.api(self.ub).post(URL + f'{offer.pk}/decide/', {'status': 'withdrawn'},
                                                format='json').status_code, 404)
        self.assertEqual(self.api(self.ua).patch(URL + f'{offer.pk}/', {'amount': 1}, format='json').status_code, 405)

    def test_validation(self):
        foreign = make_property(org=make_org(name='Elsewhere'), title='Not ours')
        for bad in ({'amount': 0}, {'amount': 'x'}, {'currency': 'PK'}, {'property': str(foreign.pk)},
                    {'expires_at': (timezone.now() - timedelta(hours=1)).isoformat()},
                    {'expires_at': (timezone.now() + timedelta(days=400)).isoformat()},
                    {'party': 'agent'}, {'basis': 'per_week'}, {'conditions': 'x' * 1001}):
            self.assertEqual(self.offer(**bad).status_code, 400, bad)
        first = self.offer().data['id']
        self.assertEqual(self.offer(previous=first, party='counterparty', currency='USD').status_code, 400)
        no_listing = self.offer(property=None)
        self.assertEqual((no_listing.status_code, no_listing.data['property']), (201, None))
        self.assertEqual(self.api(self.ua).post(URL + f'{first}/decide/', {'status': 'expired'},
                                                format='json').status_code, 400)

    def test_expiry_on_read_and_by_job(self):
        soon = timezone.now() + timedelta(hours=1)
        a = self.offer(expires_at=soon.isoformat()).data['id']
        b = self.offer(expires_at=soon.isoformat(), amount=27_000_000).data['id']
        LeadOffer.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        r = self.api(self.ua).post(URL + f'{a}/decide/', {'status': 'accepted'}, format='json')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(LeadOffer.objects.get(pk=a).status, 'expired')
        self.assertEqual(expire_lead_offers(), 1)
        self.assertEqual(LeadOffer.objects.get(pk=b).status, 'expired')

    def test_recording_moves_the_lead_to_the_offer_stage_only_forward(self):
        stages = {s.key: s for s in PL.stages(self.org)}
        self.offer()
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.stage.key, 'offer')
        Lead.objects.filter(pk=self.lead.pk).update(stage=stages['won'])
        self.offer(amount=1_000)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.stage.key, 'won')                                 # never moved back

    def test_erasure_keeps_amounts_drops_text(self):
        from apps.compliance import erasure as E
        self.offer(conditions='Private details of the buyer')
        E._lead_offers(self.lead.user)
        o = LeadOffer.objects.get()
        self.assertEqual((o.conditions, o.amount), (OF.REDACTED, 28_000_000))
