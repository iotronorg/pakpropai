"""
T-1080 — listing lifecycle (FR-LST-005/006, ADR-025).

status ↔ is_active consistency (trigger + save mirror, legacy writers),
the transition rules and who may take them, publish preconditions,
per-market review and TTL, expiry + reminders, the API (create, visibility,
transitions endpoint, legacy is_active PATCH), admin actions and the backfill.
"""
import importlib
from datetime import timedelta
from unittest.mock import MagicMock

from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.markets.models import Market
from apps.markets.services import invalidate
from apps.properties import lifecycle as lc
from apps.properties.models import ListingTransition, Property
from tests.factories import make_developer, make_property, make_user

URL = '/api/v1/properties/'
S = Property.Status


def _db(pk):
    return tuple(Property.objects.filter(pk=pk).values_list('status', 'is_active').get())


def _market(**flags):
    m = Market.objects.get(code='PK')
    m.settings = {**(m.settings or {}), **flags}
    m.save(update_fields=['settings'])
    invalidate('PK')


class _Base(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.admin = make_user(role='admin')
        self.addCleanup(invalidate, 'PK')

    def _prop(self, **kw):
        kw.setdefault('owner', self.dev)
        return make_property(org=self.org, **kw)


class ConsistencyTest(_Base):
    def test_status_drives_is_active_everywhere(self):
        p = self._prop()
        self.assertEqual(_db(p.pk), ('published', True))
        Property.objects.filter(pk=p.pk).update(status='draft')               # bulk: trigger
        self.assertEqual(_db(p.pk), ('draft', False))
        Property.objects.filter(pk=p.pk).update(status='under_offer')
        self.assertEqual(_db(p.pk), ('under_offer', True))
        Property.objects.filter(pk=p.pk).update(is_active=False)             # legacy writer
        self.assertEqual(_db(p.pk), ('archived', False))
        Property.objects.filter(pk=p.pk).update(is_active=True)
        self.assertEqual(_db(p.pk), ('published', True))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Property.objects.filter(pk=p.pk).update(status='bogus')

    def test_legacy_inserts_and_saves(self):
        old = self._prop(is_active=False)                                      # create(is_active=False)
        self.assertEqual((old.status, _db(old.pk)), ('archived', ('archived', False)))
        p = self._prop()
        p.is_active = False
        p.save(update_fields=['is_active'])                                    # test_deal_lock pattern
        self.assertEqual((p.status, _db(p.pk)), ('archived', ('archived', False)))
        t = p.transitions.first()
        self.assertEqual((t.from_status, t.to_status, t.source), ('published', 'archived', 'direct'))
        self.assertEqual(p.transitions.last().source, 'create')
        # Writers that skip save() (bulk_create, the previous release): the trigger decides.
        legacy = Property(title='L', city='Lahore', location='X', price=1, owner=self.dev,
                          organization=self.org, listing_owner_type='organization', is_active=False,
                          ref_no='RT-TEST-LEGACY-1', market_id='PK', category='residential')
        Property.objects.bulk_create([legacy])
        self.assertEqual(tuple(Property.objects.filter(ref_no='RT-TEST-LEGACY-1')
                               .values_list('status', 'is_active').get()), ('archived', False))
        draft = self._prop(status='draft')
        self.assertEqual(_db(draft.pk), ('draft', False))
        self.assertIsNotNone(Property.objects.get(pk=draft.pk).status_changed_at)


class RulesTest(_Base):
    def test_edges_and_roles(self):
        p = self._prop()
        with self.assertRaises(lc.InvalidTransition):
            lc.transition(p, S.EXPIRED, actor=self.dev)                       # system only
        with self.assertRaises(lc.InvalidTransition):
            lc.transition(p, S.LET, actor=self.dev)                           # sale listing
        with self.assertRaises(lc.TransitionForbidden):
            lc.transition(p, S.SUSPENDED, actor=self.dev)
        with self.assertRaises(lc.TransitionForbidden):
            lc.transition(p, S.ARCHIVED, actor=make_user(role='developer'))   # not theirs
        lc.transition(p, S.UNDER_OFFER, actor=self.dev, reason='offer received')
        first_published = Property.objects.get(pk=p.pk).published_at
        lc.transition(p, S.PUBLISHED, actor=self.dev)                          # fell through
        self.assertEqual(Property.objects.get(pk=p.pk).published_at, first_published)
        lc.transition(p, S.SOLD, actor=self.dev)
        with self.assertRaises(lc.InvalidTransition):
            lc.transition(p, S.PUBLISHED, actor=self.dev)                      # sold is final
        self.assertEqual(_db(p.pk), ('sold', False))
        hist = list(p.transitions.order_by('created_at', 'id').values_list('to_status', flat=True))
        self.assertEqual(hist, ['published', 'under_offer', 'published', 'sold'])
        self.assertEqual(p.transitions.get(to_status='under_offer').reason, 'offer received')

    def test_suspension_is_moderator_only(self):
        p = self._prop()
        lc.transition(p, S.SUSPENDED, actor=self.admin)
        with self.assertRaises(lc.TransitionForbidden):
            lc.transition(p, S.PUBLISHED, actor=self.dev)
        self.assertNotIn(S.PUBLISHED, lc.allowed_targets(p, self.dev))
        self.assertEqual(lc.allowed_targets(p, self.dev), [S.ARCHIVED])       # owner may withdraw
        lc.transition(p, S.PUBLISHED, actor=self.admin)
        self.assertEqual(_db(p.pk), ('published', True))

    def test_rent_and_publish_preconditions(self):
        r = self._prop(purpose='rent_long', price_type='per_month', status='draft', price=None)
        with self.assertRaises(lc.PublishBlocked) as cm:
            lc.transition(r, S.PUBLISHED, actor=self.dev)
        self.assertIn('price', cm.exception.fields)
        Property.objects.filter(pk=r.pk).update(price_on_request=True)
        r.refresh_from_db()
        lc.transition(r, S.PUBLISHED, actor=self.dev)
        lc.transition(r, S.LET, actor=self.dev)
        lc.transition(r, S.PUBLISHED, actor=self.dev)                          # re-let
        self.assertEqual(_db(r.pk), ('published', True))

    def test_review_market(self):
        _market(listing_review_required=True)
        p = self._prop(status='draft')
        with self.assertRaises(lc.InvalidTransition):
            lc.transition(p, S.PUBLISHED, actor=self.dev)
        lc.transition(p, S.PENDING_REVIEW, actor=self.dev)
        with self.assertRaises(lc.TransitionForbidden):
            lc.transition(p, S.PUBLISHED, actor=self.dev)
        lc.transition(p, S.PUBLISHED, actor=self.admin, source='admin')
        self.assertEqual(p.transitions.first().actor, self.admin)
        lc.transition(p, S.UNDER_OFFER, actor=self.dev)
        lc.transition(p, S.PUBLISHED, actor=self.dev)                          # not a new publication


class ExpiryTest(_Base):
    def test_ttl_expiry_reminder_renew(self):
        _market(listing_ttl_days=30)
        p = self._prop(status='draft')
        lc.transition(p, S.PUBLISHED, actor=self.dev)
        p.refresh_from_db()
        self.assertAlmostEqual((p.expires_at - p.published_at).days, 30)
        now = timezone.now()
        self.assertEqual(lc.remind_expiring(now), 0)                           # 30 days away
        soon = p.expires_at - timedelta(days=3)
        self.assertEqual(lc.remind_expiring(soon), 1)
        self.assertEqual(lc.remind_expiring(soon), 0)                          # once only
        from apps.notifications.models import Notification
        self.assertTrue(Notification.objects.filter(user=self.dev, title='Listing expiring soon').exists())
        self.assertEqual(lc.expire_due(p.expires_at + timedelta(minutes=1)), 1)
        self.assertEqual(_db(p.pk), ('expired', False))
        t = p.transitions.first()
        self.assertEqual((t.source, t.actor_id), ('system', None))
        p.refresh_from_db()
        lc.transition(p, S.PUBLISHED, actor=self.dev)                          # renew
        p.refresh_from_db()
        self.assertGreater(p.expires_at, now + timedelta(days=29))
        self.assertIsNone(p.expiry_reminded_at)

    def test_no_ttl_no_expiry_and_task(self):
        p = self._prop()
        self.assertIsNone(Property.objects.get(pk=p.pk).expires_at)
        from apps.properties.tasks import expire_listings
        self.assertEqual(expire_listings(), {'expired': 0, 'reminded': 0})
        Property.objects.filter(pk=p.pk).update(status='under_offer', expires_at=timezone.now())
        self.assertEqual(lc.expire_due(), 0)                                   # only published expire


class ApiTest(_Base):
    def setUp(self):
        super().setUp()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)

    def _create(self, **extra):
        body = {'title': 'N', 'city': 'Lahore', 'location': 'DHA', 'price': 1_000_000, **extra}
        return self.c.post(URL, body, format='json')

    def _go(self, pk, to, client=None, **kw):
        return (client or self.c).post(f'{URL}{pk}/transitions/', {'to': to, **kw}, format='json')

    def test_create_and_visibility(self):
        r = self._create()
        self.assertEqual(r.status_code, 201, r.data)
        pub = Property.objects.get(title='N')
        self.assertEqual(pub.status, 'published')
        self.assertIsNotNone(pub.published_at)
        t = pub.transitions.get()
        self.assertEqual((t.from_status, t.source, t.actor), ('', 'create', self.dev))
        r = self._create(title='D', status='draft')
        draft = Property.objects.get(title='D')
        self.assertEqual(_db(draft.pk), ('draft', False))
        titles = lambda q='', c=None: [x['title'] for x in (c or self.c).get(URL + q).data['results']]
        self.assertNotIn('D', titles())                                        # default: public only
        self.assertEqual(titles('?status=draft'), ['D'])
        self.assertEqual(self.c.get(f'{URL}{draft.pk}/').data['status'], 'draft')
        self.assertIn('D', [x['title'] for x in self.c.get(URL + 'mine/').data])
        other = APIClient()
        other.force_authenticate(make_user(role='client'))
        self.assertEqual(other.get(f'{URL}{draft.pk}/').status_code, 404)
        self.assertEqual(titles('?status=draft', other), [])
        self.assertEqual(self.c.get(URL + '?status=bogus').status_code, 400)
        self.assertEqual(self._create(title='X', status='sold').status_code, 400)

    def test_transitions_endpoint(self):
        p = self._prop()
        Property.objects.filter(pk=p.pk).update(is_sandbox=False)            # outsiders can see it
        r = self._go(p.pk, 'under_offer', reason='offer')
        self.assertEqual((r.status_code, r.data['status']), (200, 'under_offer'))
        self.assertEqual(self._go(p.pk, 'draft').status_code, 409)             # not an edge from under_offer
        self.assertEqual(self._go(p.pk, 'let').status_code, 409)
        self.assertEqual(self._go(p.pk, 'suspended').status_code, 403)
        self.assertEqual(self._go(p.pk, 'nope').status_code, 400)
        g = self.c.get(f'{URL}{p.pk}/transitions/').data
        self.assertEqual(g['status'], 'under_offer')
        self.assertIn('sold', g['allowed'])
        self.assertNotIn('suspended', g['allowed'])
        self.assertEqual(g['history'][0]['reason'], 'offer')
        self.assertEqual(g['history'][0]['actor_kind'], 'lister')
        self.assertNotIn('actor', g['history'][0])
        stranger = APIClient()
        stranger.force_authenticate(make_user(role='client'))
        self.assertEqual(stranger.get(f'{URL}{p.pk}/transitions/').status_code, 403)
        self.assertEqual(self._go(p.pk, 'archived', stranger).status_code, 403)
        admin = APIClient()
        admin.force_authenticate(self.admin)
        self.assertEqual(self._go(p.pk, 'suspended', admin).status_code, 200)

    def test_publish_incomplete_is_400(self):
        p = self._prop(status='draft', price=None)
        r = self._go(p.pk, 'published')
        self.assertEqual(r.status_code, 400)
        self.assertIn('price', r.data)

    def test_patch_is_active_maps_and_status_is_read_only(self):
        p = self._prop()
        r = self.c.patch(f'{URL}{p.pk}/', {'is_active': False, 'status': 'sold', 'title': 'T2'},
                         format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(_db(p.pk), ('archived', False))
        self.assertEqual(p.transitions.first().reason, 'is_active via API')
        self.assertEqual(self.c.patch(f'{URL}{p.pk}/', {'is_active': True}, format='json').status_code, 200)
        self.assertEqual(_db(p.pk), ('published', True))
        Property.objects.filter(pk=p.pk).update(status='sold')
        r = self.c.patch(f'{URL}{p.pk}/', {'is_active': True, 'title': 'T3'}, format='json')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Property.objects.get(pk=p.pk).title, 'T2')            # rolled back together

    def test_review_market_create(self):
        _market(listing_review_required=True)
        self._create(title='R')
        self.assertEqual(Property.objects.get(title='R').status, 'pending_review')


class AdminAndBackfillTest(_Base):
    def test_admin_actions(self):
        from django.contrib.admin.sites import site
        _market(listing_review_required=True)
        p = self._prop(status='pending_review')
        ma = site._registry[Property]
        req = MagicMock(user=self.admin)
        from unittest.mock import patch
        with patch('django.contrib.messages.success') as ok, patch('django.contrib.messages.warning'):
            ma.approve_publish(req, Property.objects.filter(pk=p.pk))
        ok.assert_called_once()
        self.assertEqual(_db(p.pk), ('published', True))
        self.assertEqual(p.transitions.first().source, 'admin')

    def test_backfill(self):
        live = self._prop()
        gone = self._prop()
        with connection.cursor() as cur:
            cur.execute("SET session_replication_role = replica")             # pre-trigger rows
            cur.execute("UPDATE properties SET status='published', status_changed_at=NULL, "
                        "published_at=NULL, is_active = (id = %s) WHERE id IN (%s, %s)",
                        [live.pk, live.pk, gone.pk])
            cur.execute("SET session_replication_role = DEFAULT")
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        importlib.import_module('apps.properties.migrations.0046_backfill_listing_status').forwards(
            None, connection.schema_editor())
        self.assertEqual(_db(live.pk), ('published', True))
        self.assertEqual(_db(gone.pk), ('archived', False))
        self.assertIsNotNone(Property.objects.get(pk=live.pk).published_at)
        self.assertIsNone(Property.objects.get(pk=gone.pk).published_at)
