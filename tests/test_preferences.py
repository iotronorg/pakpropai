"""T-2040 — favourites, saved searches with alerts, consented preferences (ADR-038)."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai import confirmation
from apps.ai import tool_registry as R
from apps.ai import tools
from apps.ai._tools_context import set_context
from apps.ai.models import PendingAction
from apps.compliance.models import ConsentRecord
from apps.conversations import store
from apps.conversations.models import ConversationState
from apps.core import outbox
from apps.core.models import OutboxMessage
from apps.locations.models import Location
from apps.markets.models import Market
from apps.notifications.models import Notification, UserNotificationPreference
from apps.preferences import alerts as A
from apps.preferences import services as S
from apps.preferences.models import AlertDelivery, Favourite, PreferenceItem, SavedSearch
from apps.properties.models import Property
from tests.factories import make_client, make_org, make_property
from tests.test_runtime import LOCMEM

ME = '/api/v1/me/'
CRIT = {'market': 'PK', 'location': {'free_text': 'Lahore'}, 'purpose': 'sale', 'price': {'max': 20_000_000},
        'bedrooms': {'min': 3}}


@override_settings(CACHES=LOCMEM, EMAIL_HOST='smtp.test', FRONTEND_URL='https://app.test')
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.org = make_org(operational_mode='production')
        self.user = make_client(email='me@mail.example')
        self.other = make_client()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def make(self, title='House', **kw):
        extra = {k: kw.pop(k) for k in ('is_sandbox', 'is_active', 'published_at', 'expires_at') if k in kw}
        if 'owner' not in kw:
            kw['org'] = self.org
        p = make_property(title=title, **{'bedrooms': 3, 'price': 10_000_000, **kw})
        Property.objects.filter(pk=p.pk).update(**{'is_sandbox': False, **extra})
        p.refresh_from_db()
        return p

    def as_other(self):
        c = APIClient()
        c.force_authenticate(self.other)
        return c


class FavouriteTest(Base):
    def keep(self, p, **kw):
        return self.api.post(ME + 'favourites/', {'listing_id': str(p.pk), **kw}, format='json')

    def test_save_update_list_remove(self):
        p = self.make()
        self.assertEqual(self.keep(p, note='corner').status_code, 200)
        self.assertEqual(self.keep(p).data['note'], 'corner')
        [row] = self.api.get(ME + 'favourites/').data['results']
        self.assertEqual((row['available'], row['card']['id'], Favourite.objects.count()), (True, str(p.pk), 1))
        self.keep(p, kind='dismissed')
        self.assertEqual(self.api.get(ME + 'favourites/').data['results'], [])
        self.assertEqual(len(self.api.get(ME + 'favourites/', {'kind': 'dismissed'}).data['results']), 1)
        self.assertEqual(self.api.delete(f'{ME}favourites/{p.pk}/').status_code, 204)
        self.assertEqual(self.api.delete(f'{ME}favourites/{p.pk}/').status_code, 404)

    def test_private_to_the_person(self):
        p = self.make()
        self.keep(p)
        self.assertEqual(self.as_other().get(ME + 'favourites/').data['results'], [])
        self.assertEqual(self.as_other().delete(f'{ME}favourites/{p.pk}/').status_code, 404)
        self.assertEqual(APIClient().get(ME + 'favourites/').status_code, 401)

    def test_withdrawn_listings_show_no_data(self):
        p = self.make('Secret title')
        self.keep(p)
        Property.objects.filter(pk=p.pk).update(is_active=False)
        r = self.api.get(ME + 'favourites/')
        self.assertEqual((r.data['results'][0]['available'], r.data['results'][0]['card']), (False, None))
        self.assertNotIn('Secret', r.content.decode())

    def test_refusals(self):
        self.assertEqual(self.keep(self.make(is_sandbox=True)).status_code, 404)
        self.assertEqual(self.keep(self.make(), kind='x').status_code, 400)
        self.assertEqual(self.api.get(ME + 'favourites/', {'kind': 'x'}).status_code, 400)
        with patch.object(S, 'MAX_FAVOURITES', 0):
            self.assertEqual(self.keep(self.make()).status_code, 409)


class SavedSearchApiTest(Base):
    def save(self, **kw):
        return self.api.post(ME + 'saved-searches/', {'criteria': CRIT, 'alert_consent': True, **kw}, format='json')

    def test_create(self):
        r = self.save(frequency='weekly', channel='email')
        self.assertEqual(r.status_code, 201)
        s = SavedSearch.objects.get()
        self.assertEqual((s.user, s.frequency, s.channel, s.consent_via, s.is_active), (self.user, 'weekly', 'email', 'api', True))
        self.assertEqual((s.criteria['market'], s.criteria['price']['currency']), ('PK', 'PKR'))
        self.assertIn('3+ bed', s.name)

    def test_validation(self):
        for kw in ({'alert_consent': False}, {'criteria': {'purpose': 'sale'}}, {'criteria': 'x'}, {'criteria': {'x': 1}},
                   {'frequency': 'hourly'}, {'channel': 'sms'}, {'criteria': {**CRIT, 'market': 'ZZ'}},
                   {'criteria': {**CRIT, 'amenities_required': ['nope']}}):
            self.assertEqual(self.save(**kw).status_code, 400, kw)
        with patch.object(A, 'MAX_SEARCHES', 0):
            self.assertEqual(self.save().status_code, 409)
        self.assertFalse(SavedSearch.objects.exists())

    def test_edit_pause_resume_delete_and_ownership(self):
        url = f"{ME}saved-searches/{self.save().data['id']}/"
        for method in ('get', 'patch', 'delete'):
            self.assertEqual(getattr(self.as_other(), method)(url).status_code, 404)
        r = self.api.patch(url, {'name': 'Mine', 'is_active': False}, format='json')
        self.assertEqual((r.data['name'], r.data['is_active'], r.data['paused_reason']), ('Mine', False, 'user'))
        before = SavedSearch.objects.get().since
        r = self.api.patch(url, {'is_active': True, 'criteria': {**CRIT, 'purpose': 'rent_long'}}, format='json')
        self.assertEqual((r.data['is_active'], r.data['criteria']['purpose']), (True, 'rent_long'))
        self.assertGreater(SavedSearch.objects.get().since, before)
        self.assertEqual(self.api.patch(url, {'criteria': {}}, format='json').status_code, 400)
        self.assertEqual(self.api.delete(url).status_code, 204)

    def test_stop_link(self):
        self.save()
        s = SavedSearch.objects.get()
        c = APIClient()
        self.assertEqual(c.post('/api/v1/saved-search-stop/', {'token': str(s.pk)}, format='json').status_code, 400)
        self.assertEqual(c.post('/api/v1/saved-search-stop/', {'token': A.stop_token(s)}, format='json').status_code, 204)
        s.refresh_from_db()
        self.assertEqual((s.is_active, s.paused_reason), (False, 'unsubscribed'))


class AlertTest(Base):
    def setUp(self):
        super().setUp()
        self.old = self.make('Old house')
        Property.objects.update(created_at=timezone.now() - timedelta(days=5),
                                published_at=timezone.now() - timedelta(days=5))
        self.s = A.create(self.user, CRIT, frequency='daily')

    def due(self):
        SavedSearch.objects.update(next_run_at=timezone.now() - timedelta(seconds=1))
        return A.run_due()

    def told(self):
        return {d.listing.title for d in AlertDelivery.objects.select_related('listing')}

    def test_only_new_matches_once(self):
        self.assertEqual(self.due(), 0)
        self.make('New house')
        self.make('Too small', bedrooms=1)
        self.make('Hidden', is_sandbox=True)
        self.make('Expired', expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual((self.due(), self.due()), (1, 0))
        self.assertEqual(self.told(), {'New house'})
        n = Notification.objects.get(user=self.user)
        self.assertIn('New house', n.message)
        self.assertNotIn('Old house', n.message)

    def test_price_drop_into_the_range_is_new(self):
        p = self.make('Was expensive', price=90_000_000)
        Property.objects.filter(pk=p.pk).update(published_at=timezone.now() - timedelta(days=9))
        self.due()
        self.assertEqual(self.told(), set())
        p.refresh_from_db()
        p.price = 15_000_000
        p.save()
        self.due()
        self.assertEqual(self.told(), {'Was expensive'})

    def test_saved_dismissed_and_own_listings_are_left_out(self):
        S.keep(self.user, self.make('Saved'))
        S.keep(self.user, self.make('No thanks'), kind='dismissed')
        self.make('Mine', owner=self.user)
        self.assertEqual(self.due(), 0)

    def test_not_due_paused_or_inactive_person(self):
        self.make('New house')
        self.assertEqual(A.run_due(), 0)
        A.pause(self.s, 'user')
        self.assertEqual(self.due(), 0)
        A.resume(self.s)
        self.make('Newer house')
        type(self.user).objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.due(), 0)
        type(self.user).objects.filter(pk=self.user.pk).update(is_active=True)
        self.due()
        self.assertEqual(self.told(), {'Newer house'})       # nothing from the pause

    def test_frequency(self):
        now = timezone.now()
        for f, low, high in (('instant', 10, 20), ('daily', 1430, 1450), ('weekly', 10070, 10090)):
            s = A.create(self.user, CRIT, frequency=f)
            self.assertTrue(now + timedelta(minutes=low) < s.next_run_at < now + timedelta(minutes=high), f)

    def test_instant_is_at_most_hourly(self):
        SavedSearch.objects.update(frequency='instant')
        self.make('One')
        self.assertEqual(self.due(), 1)
        self.make('Two')
        self.assertEqual(self.due(), 0)
        SavedSearch.objects.update(last_alert_at=timezone.now() - timedelta(minutes=61))
        self.assertEqual(self.due(), 1)
        self.assertEqual(self.told(), {'One', 'Two'})

    def test_email_channel_with_stop_link(self):
        SavedSearch.objects.update(channel='email')
        self.make('Call 03001234567 now', owner=self.other)
        self.due()
        [row] = OutboxMessage.objects.all()
        outbox.dispatch(row.pk)
        self.assertEqual(mail.outbox[0].to, ['me@mail.example'])
        self.assertIn('?stop=', mail.outbox[0].body)
        self.assertNotIn('03001234567', mail.outbox[0].body)

    def test_in_app_channel_and_preference(self):
        self.make('One')
        self.due()
        self.assertFalse(OutboxMessage.objects.exists())
        SavedSearch.objects.update(channel='whatsapp')
        UserNotificationPreference.objects.update(saved_searches=False)
        self.make('Two')
        self.due()
        self.assertFalse(OutboxMessage.objects.exists())

    def test_broken_criteria_pause_the_search(self):
        Market.objects.filter(code='PK').update(status='inactive')
        self.make('One')
        self.due()
        self.s.refresh_from_db()
        self.assertEqual((self.s.is_active, self.s.paused_reason, self.told()), (False, 'criteria', set()))

    def test_one_failure_does_not_stop_the_others(self):
        A.create(self.user, CRIT)
        self.make('One')
        real, calls = A.run, []

        def flaky(pk):
            calls.append(pk)
            if len(calls) == 1:
                raise RuntimeError('x')
            return real(pk)
        with patch.object(A, 'run', flaky):
            self.assertEqual(self.due(), 1)
        self.assertEqual(len(calls), 2)

    def test_failed_notification_transaction_leaves_nothing_marked(self):
        self.make('One')
        with patch('apps.notifications.models.Notification.objects.create', side_effect=RuntimeError):
            self.due()
        self.assertEqual(self.told(), set())
        self.assertEqual(self.due(), 1)                      # tried again
        self.assertEqual(self.told(), {'One'})

    def test_whatsapp_alert_has_a_stop_link(self):
        SavedSearch.objects.update(channel='whatsapp')
        self.make('One')
        self.due()
        self.assertIn('https://app.test/saved-searches/', Notification.objects.get(user=self.user).message)
        self.assertEqual(OutboxMessage.objects.count(), 1)


class ProfileTest(Base):
    def patch(self, values, market='PK'):
        return self.api.patch(ME + 'preferences/', {'market': market, 'values': values}, format='json')

    def consent(self, on):
        return self.api.put(ME + 'preferences/consent/', {'consent': on}, format='json')

    def test_nothing_is_stored_without_consent(self):
        self.assertEqual(self.patch({'purpose': 'sale'}).status_code, 403)
        for i in range(4):
            S.keep(self.user, self.make(f'H{i}'))
        self.assertFalse(PreferenceItem.objects.exists())
        self.assertEqual(self.api.get(ME + 'preferences/').data['consent'], False)

    def test_state_view_edit_delete(self):
        self.consent(True)
        r = self.patch({'purpose': 'sale', 'price': {'max': 20_000_000}, 'bedrooms': {'min': 3}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual({i['key'] for i in r.data['items']}, {'purpose', 'price', 'bedrooms'})
        self.patch({'purpose': 'rent_long'})
        self.assertEqual(PreferenceItem.objects.get(key='purpose').value, 'rent_long')
        pk = PreferenceItem.objects.get(key='purpose').pk
        self.assertEqual(self.as_other().delete(f'{ME}preferences/{pk}/').status_code, 404)
        self.assertEqual(self.api.delete(f'{ME}preferences/{pk}/').status_code, 204)
        self.assertEqual(self.api.delete(ME + 'preferences/').status_code, 204)
        self.assertFalse(PreferenceItem.objects.exists())

    def test_withdrawal_deletes_everything(self):
        self.consent(True)
        self.patch({'purpose': 'sale'})
        self.assertEqual(self.consent(False).data['items'], [])
        self.assertFalse(PreferenceItem.objects.exists())
        rec = ConsentRecord.objects.get(user=self.user, purpose='ai_memory')
        self.assertEqual((rec.is_active, rec.revoked_at is None), (False, False))
        self.assertEqual(self.consent(True).data['consent'], True)

    def test_only_structured_fields(self):
        self.consent(True)
        for values in ({'keywords': 'x'}, {'soft_preferences': {'notes': 'x'}}, {'purpose': 'x'}, {'purpose': 5},
                       {'price': {'max': -1}}, {'accessibility': ['wheelchair_access']}):
            self.assertEqual(self.patch(values).status_code, 400, values)
        self.assertEqual(self.patch({'purpose': 'sale'}, 'ZZ').status_code, 400)
        self.patch({'location': {'free_text': 'muslim only area'}})
        self.assertFalse(PreferenceItem.objects.exists())

    def test_inferred_from_saved_listings(self):
        self.consent(True)
        kept = [self.make(f'H{i}', bedrooms=3 + i, price=10_000_000 + i) for i in range(3)]
        for p in kept[:2]:
            S.keep(self.user, p)
        self.assertFalse(PreferenceItem.objects.exists())
        S.keep(self.user, kept[2])
        got = {i.key: i.value for i in PreferenceItem.objects.filter(source='inferred')}
        self.assertEqual((got['purpose'], got['category'], got['bedrooms'], got['price']['max']),
                         ('sale', 'residential', {'min': 3, 'max': 5}, 10_000_002))
        S.drop(self.user, kept[0].pk)
        self.assertFalse(PreferenceItem.objects.exists())

    def test_hidden_prices_are_not_inferred(self):
        self.consent(True)
        for i in range(3):
            S.keep(self.user, self.make(f'H{i}', price_on_request=True))
        self.assertFalse(PreferenceItem.objects.filter(key='price').exists())

    def test_stale_items_are_purged(self):
        self.consent(True)
        self.patch({'purpose': 'sale', 'category': 'residential'})
        PreferenceItem.objects.filter(key='purpose').update(updated_at=timezone.now() - timedelta(days=181))
        self.assertEqual((S.purge(), PreferenceItem.objects.get().key), (1, 'category'))

    def test_export(self):
        from apps.compliance.models import DataExportRequest
        from apps.compliance.tasks import generate_data_export
        self.consent(True)
        self.patch({'purpose': 'sale'})
        S.keep(self.user, self.make(), note='corner')
        A.create(self.user, CRIT)
        req = DataExportRequest.objects.create(user=self.user)
        with patch('cloudinary.uploader.upload', return_value={'secure_url': 'https://x', 'public_id': 'x'}) as up:
            generate_data_export.apply(args=[str(req.pk)])
        body = up.call_args.args[0]
        data = json.loads(body.getvalue() if hasattr(body, 'getvalue') else body)
        self.assertEqual((data['favourites'][0]['note'], len(data['saved_searches']), data['preferences'][0]['key']),
                         ('corner', 1, 'purpose'))

    def test_erasure(self):
        self.consent(True)
        self.patch({'purpose': 'sale'})
        S.keep(self.user, self.make())
        A.create(self.user, CRIT)
        from apps.compliance.erasure import RTBFOrchestrator
        from apps.compliance.models import DataDeletionRequest
        RTBFOrchestrator().execute(DataDeletionRequest.objects.create(user=self.user))
        self.assertFalse(Favourite.objects.exists() or SavedSearch.objects.exists() or PreferenceItem.objects.exists())
        self.assertFalse(S.remembers(self.user))


class ToolTest(Base):
    def setUp(self):
        super().setUp()
        self.phone = self.user.phone.lstrip('+')
        self.conv = store.open_whatsapp(self.phone, user=self.user)
        self.lahore, _ = Location.objects.get_or_create(market_id='PK', name='Lahore', level='city',
                                                        defaults={'match_key': 'lahore', 'path': 'zz.lahore'})

    def ctx(self, message='', user='me'):
        set_context(self.user if user == 'me' else user, self.phone, message=message)

    def search(self, criteria=CRIT):
        ConversationState.objects.update_or_create(conversation=self.conv, defaults={'active_criteria': criteria})

    def propose(self, tool, **args):
        self.ctx()
        return confirmation.propose(R.REGISTRY[tool], args)

    def yes(self):
        return confirmation.resolve_reply(self.phone, 'yes', user=self.user)

    def test_save_listing(self):
        p = self.make()
        self.ctx()
        self.assertTrue(tools.save_listing(p.ref_no)['saved'])
        self.assertFalse(tools.save_listing('nope')['saved'])
        self.ctx(user=None)
        self.assertFalse(tools.save_listing(p.ref_no)['saved'])
        self.assertEqual(Favourite.objects.get().user, self.user)

    def test_saved_search_after_yes_with_the_shown_criteria(self):
        self.search()
        r = self.propose('create_saved_search', frequency='weekly', channel='in_app', criteria='{"market": "AE"}')
        for shown in ('Weekly', '20000000', 'Lahore', 'min 3'):
            self.assertIn(shown, r['message'])
        self.assertFalse(SavedSearch.objects.exists())
        self.search({**CRIT, 'bedrooms': {'min': 9}})           # changed after the summary
        self.assertIn('Saved', self.yes())
        s = SavedSearch.objects.get()
        self.assertEqual((s.criteria['market'], s.criteria['bedrooms'], s.frequency, s.consent_via),
                         ('PK', {'min': 3}, 'weekly', 'assistant'))

    def test_saved_search_refusals(self):
        self.assertIn('error', self.propose('create_saved_search'))          # nothing searched
        self.search()
        self.assertIn('error', self.propose('create_saved_search', frequency='hourly'))
        type(self.user).objects.update(email='')
        self.user.refresh_from_db()
        self.assertIn('error', self.propose('create_saved_search', channel='email'))
        self.assertFalse(PendingAction.objects.exists())

    def test_remember_is_the_consent(self):
        self.search({**CRIT, 'keywords': 'secret words'})
        r = self.propose('remember_preferences')
        self.assertIn(S.MEMORY_TEXT, r['message'])
        self.assertNotIn('secret', r['message'])
        self.assertFalse(S.remembers(self.user))
        self.yes()
        self.assertTrue(S.remembers(self.user))
        self.assertEqual(S.stated(self.user, 'PK')['bedrooms'], {'min': 3})
        self.assertNotIn('secret', json.dumps(list(PreferenceItem.objects.values('value'))))

    def test_a_failed_remember_leaves_no_consent(self):
        self.search()
        self.propose('remember_preferences')
        with patch.object(S, 'state', side_effect=S.PreferenceError('x', 'y')):
            self.yes()
        self.assertFalse(S.remembers(self.user))

    def test_profile_fills_what_is_missing_and_says_so(self):
        S.set_memory(self.user, True)
        S.state(self.user, 'PK', {'purpose': 'sale', 'price': {'max': 12_000_000},
                                  'location': {'location_ids': [self.lahore.pk]}})
        self.make('Fits')
        self.make('Too expensive', price=15_000_000)
        self.ctx('houses within my budget')
        r = tools.search_listings(min_bedrooms=3)
        self.assertEqual(r['from_saved_preferences'], ['location', 'purpose', 'price'])
        self.assertEqual([x['title'] for x in r['properties']], ['Fits'])
        self.ctx('houses in lahore')
        self.assertEqual(tools.search_listings(location='Lahore', purpose='buy')['count'], 2)
        self.assertNotIn('from_saved_preferences', tools.search_listings(location='Lahore', purpose='buy'))

    def test_profile_is_not_used_without_consent_or_for_others(self):
        S.set_memory(self.user, True)
        S.state(self.user, 'PK', {'location': {'location_ids': [self.lahore.pk]}})
        self.ctx(user=self.other)
        self.assertTrue(tools.search_listings(purpose='buy')['needs_clarification'])
        self.ctx(user=None)
        self.assertTrue(tools.search_listings(purpose='buy')['needs_clarification'])
        ConsentRecord.objects.update(is_active=False)
        self.ctx()
        self.assertTrue(tools.search_listings(purpose='buy')['needs_clarification'])
