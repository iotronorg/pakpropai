"""
T-1070c — PriceHistory: recorded on create and on every change of the price
terms, attributed via price_change_context, backfilled, and readable at
/properties/{id}/price-history/ with the listing's visibility.
"""
import importlib

from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.properties.models import PriceHistory, Property
from apps.properties.price_history import price_change_context
from tests.factories import make_developer, make_property, make_user

URL = '/api/v1/properties/'


def _rows(p):
    return list(PriceHistory.objects.filter(property=p).order_by('changed_at', 'id')
                .values_list('price', 'price_type', 'source'))


class RecordingTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()

    def test_create_records_initial_row(self):
        p = make_property(org=self.org, price=5_000_000)
        self.assertEqual(_rows(p), [(5_000_000, 'total', 'system')])

    def test_unpriced_listing_has_no_row_until_priced(self):
        p = make_property(org=self.org, price=None)
        self.assertEqual(_rows(p), [])
        p.price_on_request = True
        p.save()
        self.assertEqual(len(_rows(p)), 1)

    def test_change_records_and_noop_does_not(self):
        p = Property.objects.get(pk=make_property(org=self.org, price=100).pk)
        p.title = 'renamed'
        p.save()
        self.assertEqual(len(_rows(p)), 1)
        p.price = 90
        p.save()
        p.save()                                     # unchanged again
        self.assertEqual([r[0] for r in _rows(p)], [100, 90])

    def test_update_fields_without_price_skips(self):
        p = Property.objects.get(pk=make_property(org=self.org, price=100).pk)
        p.price = 80                                 # in memory only
        p.save(update_fields=['title'])
        self.assertEqual(len(_rows(p)), 1)
        p.save(update_fields=['price'])
        self.assertEqual([r[0] for r in _rows(p)], [100, 80])

    def test_deferred_load_compares_with_last_row(self):
        pk = make_property(org=self.org, price=100).pk
        p = Property.objects.only('id', 'title').get(pk=pk)
        p.save()                                     # price not loaded or set → nothing
        p = Property.objects.defer('price').get(pk=pk)
        p.price = 100
        p.save()                                     # equals the last row → nothing
        p.price = 70
        p.save()
        self.assertEqual([r[0] for r in _rows(Property(pk=pk))], [100, 70])

    def test_context_attribution(self):
        with price_change_context(user=self.dev, source='admin'):
            p = make_property(org=self.org, price=1)
        row = PriceHistory.objects.get(property=p)
        self.assertEqual((row.changed_by, row.source), (self.dev, 'admin'))
        p.price = 2
        p.save()                                     # outside any context
        row = PriceHistory.objects.filter(property=p).first()
        self.assertEqual((row.price, row.changed_by, row.source), (2, None, 'system'))

    def test_listing_and_history_roll_back_together(self):
        from django.db import transaction
        p = make_property(org=self.org, price=100)
        try:
            with transaction.atomic():
                p.price = 50
                p.save()
                raise RuntimeError
        except RuntimeError:
            pass
        self.assertEqual(Property.objects.get(pk=p.pk).price, 100)
        self.assertEqual(len(_rows(p)), 1)


class BackfillTest(TestCase):
    def test_backfill_is_idempotent_and_dated_at_creation(self):
        _, org = make_developer()
        priced = make_property(org=org, price=100, location='A')
        unpriced = make_property(org=org, price=None, location='B')
        PriceHistory.objects.all().delete()
        mig = importlib.import_module('apps.properties.migrations.0030_backfill_price_history')
        with connection.cursor() as cur:
            cur.execute(mig.FORWARD)
            cur.execute(mig.FORWARD)
        rows = PriceHistory.objects.filter(property=priced)
        self.assertEqual(rows.count(), 1)
        self.assertEqual((rows[0].source, rows[0].changed_at), ('backfill', priced.created_at))
        self.assertFalse(PriceHistory.objects.filter(property=unpriced).exists())


class ApiTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)

    def test_api_writes_are_attributed_and_readable(self):
        r = self.c.post(URL, {'title': 'H', 'city': 'Lahore', 'location': 'DHA', 'price': 1000},
                        format='json')
        self.assertEqual(r.status_code, 201, r.data)
        p = Property.objects.get(title='H')
        p.owner = self.dev
        p.save(update_fields=['owner'])
        r = self.c.patch(f'{URL}{p.pk}/', {'price': 900}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        rows = PriceHistory.objects.filter(property=p)
        self.assertEqual({(x.source, x.changed_by_id) for x in rows}, {('api', self.dev.pk)})

        r = self.c.get(f'{URL}{p.pk}/price-history/')
        self.assertEqual(r.status_code, 200)
        data = r.data['results'] if isinstance(r.data, dict) else r.data
        self.assertEqual([d['price'] for d in data], [900, 1000])       # newest first
        self.assertNotIn('changed_by', data[0])

    def test_history_follows_listing_visibility(self):
        _, other_org = make_developer(org_name='Other')
        hidden = make_property(org=other_org, price=5, is_sandbox=True)
        Property.objects.filter(pk=hidden.pk).update(is_sandbox=True)
        outsider = APIClient()
        outsider.force_authenticate(make_user(role='client'))
        r = outsider.get(f'{URL}{hidden.pk}/price-history/')
        self.assertEqual(r.status_code, 404)
