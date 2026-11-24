"""T-2014 — listing review, moderation queue, user reports, appeals (ADR-040)."""
import importlib
from datetime import timedelta

from django.apps import apps as django_apps
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai import confirmation, tools
from apps.ai import tool_registry as R
from apps.ai._tools_context import set_context
from apps.markets.models import Market
from apps.moderation import checks
from apps.moderation import services as M
from apps.moderation.models import ModerationItem, UserReport
from apps.notifications.models import Notification
from apps.properties import lifecycle as lc
from apps.properties.models import Property
from tests.factories import make_client, make_developer, make_property
from tests.test_runtime import LOCMEM

URL, MOD = '/api/v1/properties/', '/api/v1/moderation/'
S = Property.Status
BODY = {'title': 'Corner house', 'city': 'Lahore', 'location': 'Gulberg', 'price': 10_000_000, 'category': 'residential',
        'purpose': 'sale', 'size_value': 10, 'size_unit': 'marla', 'description': 'Sunny'}


def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@override_settings(CACHES=LOCMEM)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.person, self.other = make_client(), make_client()
        self.mod = make_client()
        self.mod.role, self.mod.platform_role = 'admin', 'ops_admin'
        self.mod.save()
        self.dev, self.org = make_developer()
        self.me = client(self.person)

    def draft(self, owner=None, **kw):
        return make_property(owner=owner or self.person, status='draft', **kw)

    def waiting(self, **kw):
        p = self.draft(**kw)
        lc.transition(p, S.PENDING_REVIEW, actor=p.owner)
        return p

    def public(self, **kw):
        p = self.waiting(**kw)
        self.decide(p, 'approve')
        p.refresh_from_db()
        return p

    def item(self, p):
        return ModerationItem.objects.get(listing=p, status='open')

    def decide(self, p, decision, by=None, **kw):
        p.refresh_from_db()
        return M.decide(self.item(p).pk, by or self.mod, decision, seen=M.digest(p), **kw)

    def status(self, p):
        return Property.objects.get(pk=p.pk).status


class ReviewTest(Base):
    def test_private_listing_waits_for_review(self):
        p = self.draft()
        self.assertEqual(self.me.post(f'{URL}{p.pk}/transitions/', {'to': 'published'}, format='json').status_code, 409)
        self.assertEqual(self.me.post(f'{URL}{p.pk}/transitions/', {'to': 'pending_review'}, format='json').status_code, 200)
        p.refresh_from_db()
        self.assertEqual((p.status, p.is_active, self.item(p).reason), ('pending_review', False, 'review'))
        self.assertEqual(self.me.post(f'{URL}{p.pk}/transitions/', {'to': 'published'}, format='json').status_code, 403)
        self.assertNotEqual(self.me.patch(f'{URL}{p.pk}/', {'is_active': True}, format='json').status_code, 200)
        self.assertFalse(Property.objects.visible_to(None).filter(pk=p.pk).exists())

    def test_organization_publishes_directly_unless_flagged(self):
        c = client(self.dev)
        self.assertEqual(c.post(URL, BODY, format='json').status_code, 201)
        c.post(URL, {**BODY, 'title': 'Flat', 'description': 'Muslims only'}, format='json')
        got = dict(Property.objects.filter(organization=self.org).values_list('title', 'status'))
        self.assertEqual(got, {'Corner house': 'published', 'Flat': 'pending_review'})
        self.assertEqual(ModerationItem.objects.get().flags, ['prohibited_content'])

    def test_staff_publish_directly(self):
        p = self.draft()
        lc.transition(p, S.PUBLISHED, actor=self.mod)
        self.assertEqual(self.status(p), 'published')

    def test_renewal_without_changes_needs_no_second_review(self):
        p = self.public()
        lc.transition(p, S.ARCHIVED, actor=self.person)
        lc.transition(p, S.PUBLISHED, actor=self.person)
        lc.transition(p, S.ARCHIVED, actor=self.person)
        p.title = 'Something else'
        p.save()
        with self.assertRaises(lc.InvalidTransition):
            lc.transition(p, S.PUBLISHED, actor=self.person)

    def test_accepted_flags_do_not_return(self):
        p = self.waiting(description='see www.example.com')
        self.assertEqual(self.item(p).flags, ['external_link'])
        self.decide(p, 'approve')
        p.refresh_from_db()
        lc.transition(p, S.ARCHIVED, actor=self.person)
        lc.transition(p, S.PUBLISHED, actor=self.person)
        self.assertEqual(self.status(p), 'published')

    def test_suspension_cannot_be_escaped_through_the_archive(self):
        p = make_property(org=self.org)
        p.listed_by_agent = None
        p.owner = self.dev
        p.save()
        lc.transition(p, S.SUSPENDED, actor=self.mod)
        lc.transition(p, S.ARCHIVED, actor=self.dev)
        with self.assertRaises(lc.InvalidTransition):
            lc.transition(p, S.PUBLISHED, actor=self.dev)
        lc.transition(p, S.PENDING_REVIEW, actor=self.dev)
        self.decide(p, 'approve')
        self.assertEqual(self.status(p), 'published')

    def test_market_setting_still_reviews_everyone(self):
        m = Market.objects.get(code='PK')
        m.settings = {**m.settings, 'listing_review_required': True}
        m.save()
        p = make_property(org=self.org, owner=self.dev, status='draft')
        self.assertTrue(lc.review_required(p))

    def test_withdrawing_closes_the_item(self):
        p = self.waiting()
        lc.transition(p, S.DRAFT, actor=self.person)
        self.assertEqual(ModerationItem.objects.get().status, 'withdrawn')

    def test_django_admin_approval_closes_the_item(self):
        p = self.waiting()
        lc.transition(p, S.PUBLISHED, actor=self.mod, source='admin')
        self.assertEqual((ModerationItem.objects.get().status, ModerationItem.objects.get().decided_by),
                         ('approved', self.mod))

    def test_backfill(self):
        p = self.waiting()
        ModerationItem.objects.all().delete()
        mig = importlib.import_module('apps.moderation.migrations.0002_queue_waiting_listings')
        mig.forwards(django_apps, None)
        mig.forwards(django_apps, None)
        self.assertEqual(self.item(p).reason, 'review')


class EditTest(Base):
    def patch(self, p, **kw):
        return self.me.patch(f'{URL}{p.pk}/', kw, format='json')

    def test_flagged_edit_hides_the_listing(self):
        p = self.public()
        self.assertEqual(self.patch(p, description='No bachelors').status_code, 200)
        self.assertEqual(self.status(p), 'pending_review')
        self.assertEqual(self.item(p).flags, ['prohibited_content'])

    def test_private_text_edit_is_queued_but_stays_public(self):
        p = self.public()
        self.patch(p, description='New text')
        self.assertEqual((self.status(p), self.item(p).reason), ('published', 'edited'))
        self.decide(p, 'approve')
        self.assertEqual(self.status(p), 'published')
        self.patch(p, price=11_000_000)
        self.assertFalse(ModerationItem.objects.filter(status='open').exists())      # the price has its own check

    def test_staff_edit_is_not_queued(self):
        p = self.public()
        client(self.mod).patch(f'{URL}{p.pk}/', {'description': 'Fixed'}, format='json')
        self.assertFalse(ModerationItem.objects.filter(status='open').exists())


class ChecksTest(Base):
    def test_text(self):
        for text, code in (('Muslims only', True), ('no foreigners please', True), ('no pets', False),
                           ('near the Christian school', False), ('Family house', False), ('Black gate, white walls', False)):
            p = Property(title='H', description=text)
            self.assertEqual('prohibited_content' in checks.run(p), code, text)

    def test_market_terms(self):
        m = Market.objects.get(code='PK')
        m.settings = {**m.settings, 'prohibited_terms': ['token first', 5, '']}
        m.save()
        self.assertIn('prohibited_content', checks.run(Property(title='Token first!', description='', market_id='PK')))
        self.assertEqual(checks.run(Property(title='tokens firstly', description='', market_id='PK')), [])

    def test_price_outlier(self):
        def make(price, **kw):
            p = make_property(org=self.org, price=price, size_value=100, size_unit='sqm', **kw)
            Property.objects.filter(pk=p.pk).update(is_sandbox=False)
            return p
        for i in range(10):
            make(10_000_000 + i, title=f'T{i}')
        self.assertIn('price_outlier', checks.run(make(100_000, title='cheap')))
        self.assertIn('price_outlier', checks.run(make(200_000_000, title='dear')))
        self.assertEqual(checks.run(make(12_000_000, title='fine')), [])
        self.assertEqual(checks.run(make(100_000, title='rent', purpose='rent_long', price_type='per_month')), [])

    def test_duplicate(self):
        a = make_property(org=self.org, title='Same')
        Property.objects.filter(pk=a.pk).update(is_sandbox=False)
        self.assertEqual(checks.run(self.draft(title='same')), ['possible_duplicate'])
        self.assertEqual(checks.run(self.draft(title='same', price=1)), [])


class QueueApiTest(Base):
    def api(self):
        return client(self.mod)

    def post(self, item, by=None, **kw):
        item.listing.refresh_from_db()
        return client(by or self.mod).post(f'{MOD}items/{item.pk}/decision/',
                                           {'seen': M.digest(item.listing), **kw}, format='json')

    def test_only_moderators(self):
        item = self.item(self.waiting())
        support = make_client()
        support.role, support.platform_role = 'admin', 'support_admin'
        support.save()
        for user in (self.person, self.dev, support):
            for path in ('queues/', 'items/', f'items/{item.pk}/'):
                self.assertEqual(client(user).get(MOD + path).status_code, 403)
            self.assertEqual(self.post(item, by=user, decision='approve').status_code, 403)
        self.assertEqual(APIClient().get(MOD + 'items/').status_code, 401)

    def test_queue_order_and_filters(self):
        a, b = self.waiting(title='A'), self.waiting(title='B', description='www.x.com')
        rows = self.api().get(MOD + 'items/').data['results']
        self.assertEqual([r['title'] for r in rows], ['B', 'A'])
        self.assertEqual(self.api().get(MOD + 'items/', {'reason': 'reported'}).data['results'], [])
        self.assertEqual(self.api().get(MOD + 'items/', {'status': 'x'}).status_code, 400)
        ModerationItem.objects.filter(listing=a).update(due_at=timezone.now() - timedelta(hours=1))
        q = self.api().get(MOD + 'queues/').data
        self.assertEqual((q['review'], q['reported'], q['overdue']), (2, 0, 1))
        self.assertEqual(self.api().get(f'{MOD}items/{self.item(b).pk}/').data['digest'], M.digest(b))

    def test_approve_reject_suspend(self):
        for decision, kw, want in (('approve', {}, 'published'), ('reject', {'reason_code': 'price'}, 'draft'),
                                   ('suspend', {'reason_code': 'fraud', 'note': 'Sorry'}, 'suspended')):
            p = self.waiting(title=decision)
            r = self.post(self.item(p), decision=decision, **kw)
            self.assertEqual((r.status_code, self.status(p)), (200, want))
            self.assertEqual(p.transitions.first().actor, self.mod)
        self.assertEqual(Notification.objects.filter(user=self.person, title='Listing review').count(), 3)

    def test_refusals(self):
        p = self.waiting()
        item = self.item(p)
        self.assertEqual(self.post(item, decision='reject').status_code, 400)              # needs a reason
        self.assertEqual(self.post(item, decision='dismiss').status_code, 409)
        Property.objects.filter(pk=p.pk).update(description='changed meanwhile')
        self.assertEqual(client(self.mod).post(f'{MOD}items/{item.pk}/decision/',
                         {'decision': 'approve', 'seen': M.digest(p)}, format='json').status_code, 409)
        self.assertEqual(self.status(p), 'pending_review')
        self.assertEqual(self.post(item, decision='approve').status_code, 200)
        self.assertEqual(self.post(item, decision='suspend', reason_code='fraud').status_code, 409)

    def test_own_listing(self):
        p = self.waiting(owner=self.mod)
        self.assertEqual(self.post(self.item(p), decision='approve').status_code, 403)

    def test_overdue_is_announced_once(self):
        self.waiting()
        ModerationItem.objects.update(due_at=timezone.now() - timedelta(hours=1))
        self.assertEqual((M.notify_overdue(), M.notify_overdue()), (1, 0))
        self.assertEqual(Notification.objects.filter(user=self.mod, title='Moderation overdue').count(), 1)


class ReportTest(Base):
    def report(self, p, user=None, **kw):
        return client(user or self.other).post('/api/v1/listing-reports/', {
            'listing_id': str(p.pk), 'category': 'wrong', **kw}, format='json')

    def test_report_is_queued_and_does_not_hide(self):
        p = self.public()
        self.assertEqual(self.report(p, text='Not for sale').status_code, 202)
        self.assertEqual(self.report(p, text='again').status_code, 202)
        item = self.item(p)
        self.assertEqual((item.reason, item.priority, UserReport.objects.count(), self.status(p)),
                         ('reported', 0, 1, 'published'))
        body = client(self.mod).get(f'{MOD}items/{item.pk}/').content.decode()
        self.assertIn('again', body)
        self.assertNotIn(self.other.phone, body)
        self.assertNotIn(str(self.other.pk), body)

    def test_fraud_or_many_reports_go_first(self):
        p, q = self.public(title='P'), self.public(title='Q')
        self.report(p, category='fraud')
        self.assertEqual(self.item(p).priority, 2)
        for _ in range(3):
            self.assertEqual(self.item(q).priority if ModerationItem.objects.filter(listing=q, status='open') else 0, 0)
            self.report(q, user=make_client())
        self.assertEqual(self.item(q).priority, 2)

    def test_refusals(self):
        p = self.public()
        self.assertEqual(self.report(p, user=self.person).status_code, 403)
        self.assertEqual(self.report(self.waiting(title='hidden')).status_code, 404)
        self.assertEqual(self.report(p, category='x').status_code, 400)
        self.assertEqual(APIClient().post('/api/v1/listing-reports/', {}).status_code, 401)

    def test_moderator_outcomes(self):
        p = self.public()
        self.report(p)
        self.decide(p, 'dismiss')
        self.assertEqual(self.status(p), 'published')
        self.report(p)
        self.decide(p, 'suspend', reason_code='fraud')
        self.assertEqual(self.status(p), 'suspended')

    def test_withdrawn_listing_keeps_its_reports(self):
        p = self.public()
        self.report(p)
        lc.transition(p, S.ARCHIVED, actor=self.person)
        self.assertEqual(self.item(p).reason, 'reported')

    def test_erasure_keeps_the_fact_only(self):
        p = self.public()
        self.report(p, text='my number is secret')
        M.erase_user(self.other)
        r = UserReport.objects.get()
        self.assertEqual((r.reporter, r.text), (None, '[REDACTED]'))


class AppealTest(Base):
    def appeal(self, p, user=None):
        return client(user or self.person).post(MOD + 'appeals/', {'listing_id': str(p.pk), 'text': 'It is real'},
                                                format='json').status_code

    def test_once_per_suspension(self):
        p = self.public()
        self.assertEqual(self.appeal(p), 409)
        lc.transition(p, S.SUSPENDED, actor=self.mod)
        self.assertEqual(self.appeal(p, self.other), 404)
        self.assertEqual((self.appeal(p), self.appeal(p)), (202, 409))
        self.assertEqual(self.item(p).reason, 'appeal')
        self.decide(p, 'dismiss')
        self.assertEqual((self.status(p), self.appeal(p)), ('suspended', 409))

    def test_upheld_appeal_reinstates(self):
        p = self.public()
        lc.transition(p, S.SUSPENDED, actor=self.mod)
        self.appeal(p)
        self.decide(p, 'approve')
        self.assertEqual(self.status(p), 'published')
        lc.transition(p, S.SUSPENDED, actor=self.mod)
        self.assertEqual(self.appeal(p), 202)


class ToolTest(Base):
    ARGS = dict(city='Lahore', location='DHA Phase 6', area_marla=10, price=25_000_000, property_type='residential')

    def yes(self, tool, **args):
        phone = self.person.phone.lstrip('+')
        set_context(self.person, phone)
        r = confirmation.propose(R.REGISTRY[tool], args)
        return r, confirmation.resolve_reply(phone, 'yes', user=self.person)

    def test_draft_then_review(self):
        _, reply = self.yes('list_property', **self.ARGS)
        p = Property.objects.get(owner=self.person)
        self.assertEqual((p.status, p.is_active), ('draft', False))
        self.assertIn('not visible', reply)
        asked, reply = self.yes('submit_listing')
        self.assertIn('25,000,000', asked['message'])
        self.assertIn('sent for review', reply)
        self.assertEqual((self.status(p), self.item(p).reason), ('pending_review', 'review'))

    def test_submit_is_only_for_own_drafts(self):
        theirs = self.draft(owner=self.other)
        for ref in ('', str(theirs.pk), theirs.ref_no):
            self.assertIn('error', self.yes('submit_listing', listing=ref)[0])
        self.assertEqual(self.status(theirs), 'draft')

    def test_direct_call_only_proposes(self):
        p = self.draft()
        set_context(self.person, self.person.phone.lstrip('+'))
        self.assertTrue(tools.submit_listing()['confirmation_required'])
        self.assertEqual(self.status(p), 'draft')
