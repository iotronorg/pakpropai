"""T-2031 — public listing read API (GAP-002, ADR-035)."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.markets.models import Market
from apps.properties import public_listings as P
from apps.properties.models import Amenity, Property, PropertyImage
from tests.factories import make_client, make_org, make_property
from tests.test_runtime import LOCMEM

URL = '/api/v1/public/listings/'


@override_settings(CACHES=LOCMEM)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.org = make_org(name='Prime Homes', operational_mode='production', phone='+923001110000',
                            email='boss@prime.example')
        self.api = APIClient()

    def make(self, title='Corner House', **kw):
        extra = {k: kw.pop(k) for k in ('is_sandbox', 'status', 'is_active', 'expires_at') if k in kw}
        if 'owner' not in kw:
            kw['org'] = self.org
        p = make_property(title=title, **{'bedrooms': 3, **kw})
        Property.objects.filter(pk=p.pk).update(**{'is_sandbox': False, **extra})
        p.refresh_from_db()
        return p

    def get(self, p_or_path='', **kw):
        path = p_or_path.pk if isinstance(p_or_path, Property) else p_or_path
        return self.api.get(f'{URL}{path}/' if path else URL + 'search/', **kw)


class DetailTest(Base):
    # A new public field must be added here on purpose.
    KEYS = {'id', 'ref_no', 'slug', 'title', 'city', 'location', 'price', 'currency', 'price_type', 'purpose',
            'category', 'subtype', 'size', 'bedrooms', 'bathrooms', 'verified', 'image', 'description', 'status',
            'country', 'size_m2', 'plot_size_m2', 'floor', 'total_floors', 'year_built', 'parking_spaces',
            'pets_allowed', 'furnishing', 'completion', 'installments', 'price_on_request', 'price_negotiable',
            'deposit', 'service_charge', 'amenities', 'attributes', 'images', 'latitude', 'longitude',
            'location_precision', 'location_path', 'lister', 'published_at', 'updated_at'}

    def test_exact_field_list(self):
        self.assertEqual(set(self.get(self.make()).data), self.KEYS)

    def test_lookup_forms(self):
        p = self.make('Corner House, Gulberg!')
        slug = P.slug(p)
        self.assertEqual(slug, f'corner-house-gulberg-{p.ref_no.lower()}')
        for path in (str(p.pk), p.ref_no, slug, f'old-title-{p.ref_no.lower()}', slug.upper()):
            r = self.get(path)
            self.assertEqual((r.status_code, r.data.get('slug')), (200, slug), path)
        for path in ('nope', 'corner-house', 'x' * 300, "1'--", 'RT-LHR-2026-999999999'):
            self.assertIn(self.get(path).status_code, (403, 404), path)

    def test_unsluggable_title(self):
        p = self.make('گھر برائے فروخت')
        self.assertEqual(self.get(P.slug(p)).data['id'], str(p.pk))

    def test_not_public_is_404(self):
        for kw in ({'is_sandbox': True}, {'status': 'suspended', 'is_active': False},
                   {'status': 'draft', 'is_active': False}, {'status': 'sold', 'is_active': False},
                   {'expires_at': timezone.now() - timedelta(minutes=1)}):
            p = self.make(**kw)
            for path in (p.pk, p.ref_no, P.slug(p)):
                self.assertEqual(self.get(str(path)).status_code, 404, kw)
        p = self.make()
        Market.objects.filter(code='PK').update(status='inactive')
        self.assertEqual(self.get(p).status_code, 404)
        Property.objects.filter(pk=p.pk).update(market=None)
        self.assertEqual(self.get(p).status_code, 404)

    def test_credentials_do_not_change_the_answer(self):
        p = self.make(is_sandbox=True)
        owner = make_client()
        Property.objects.filter(pk=p.pk).update(owner=owner)
        self.api.force_login(owner)
        self.assertEqual(self.get(p).status_code, 404)
        self.assertEqual(self.api.get(f'{URL}{p.pk}/', HTTP_AUTHORIZATION='Bearer junk').status_code, 404)

    def test_hidden_price_and_money_terms(self):
        p = self.make(price=77_000_001, price_on_request=True, deposit=77_000_002, service_charge=77_000_003)
        r = self.get(p)
        self.assertNotIn('77000', json.dumps(json.loads(r.content)))
        self.assertNotIn('77000', self.get().content.decode())

    def test_private_seller(self):
        seller = make_client(phone='+923005556677', name='Private Person')
        p = self.make('House call 0300-5556677', owner=seller, latitude='31.512345', longitude='74.312345',
                      location_precision='exact',
                      description='Price 25,000,000 or 25000000. Call +92 300 555 6677 or me@mail.example, built 2015.')
        for body in (self.get(p).content.decode(), self.get().content.decode()):
            for private in ('5556677', '555 6677', 'me@mail', 'Private Person', '31.512345', str(seller.pk) + '"'):
                self.assertNotIn(private, body)
        d = self.get(p).data
        self.assertEqual(d['description'],
                         'Price 25,000,000 or 25000000. Call [contact hidden] or [contact hidden], built 2015.')
        self.assertEqual((d['latitude'], d['lister']), (31.51, {'type': 'client', 'name': None, 'slug': None}))
        self.assertNotIn('5556677', d['slug'])

    def test_organization_lister_without_contacts_or_staff(self):
        p = self.make(description='Office 042-111-222-333')
        d = self.get(p)
        self.assertEqual(d.data['lister'], {'type': 'organization', 'name': 'Prime Homes', 'slug': self.org.slug})
        self.assertIn('042-111-222-333', d.data['description'])
        for private in ('boss@prime', '+923001110000', 'raw_docs', 'ai_analysis', 'ai_score', 'risk_level'):
            self.assertNotIn(private, d.content.decode())

    def test_only_schema_attributes(self):
        Market.objects.filter(code='PK').update(listing_schema={
            'fields': {'possession': {'type': 'string'}}, 'additional': 'allow'})
        from apps.markets.services import invalidate
        invalidate('PK')
        self.addCleanup(invalidate, 'PK')
        p = self.make()
        Property.objects.filter(pk=p.pk).update(attributes={'possession': 'yes', 'owner_cnic': '35202-1234567-1'})
        self.assertEqual(self.get(p).data['attributes'], {'possession': 'yes'})

    def test_unsanitised_images_are_not_served(self):
        p = self.make()
        PropertyImage.objects.create(property=p, image='a/old.jpg', order=0)
        PropertyImage.objects.create(property=p, image='a/new.jpg', sha256='x' * 64, alt_text='Front', order=1)
        d = self.get(p).data
        self.assertEqual([i['alt_text'] for i in d['images']], ['Front'])
        self.assertTrue(d['image']['url'].startswith('http'))
        self.assertNotIn('old.jpg', self.get(p).content.decode() + self.get().content.decode())

    def test_inactive_amenities_are_left_out(self):
        p = self.make()
        for key, on in (('gym', True), ('zz_retired', False)):
            a, _ = Amenity.objects.get_or_create(key=key, defaults={'group': 'building', 'labels': {}})
            Amenity.objects.filter(pk=a.pk).update(is_active=on)
            p.amenities.add(a)
        self.assertEqual([a['key'] for a in self.get(p).data['amenities']], ['gym'])


class CacheTest(Base):
    def test_etag_and_304(self):
        p = self.make()
        r = self.get(p)
        self.assertEqual(r['Cache-Control'], 'public, max-age=60')
        self.assertNotIn('Set-Cookie', r)
        again = self.get(p, HTTP_IF_NONE_MATCH=f'W/"other", {r["ETag"]}')
        self.assertEqual((again.status_code, again.content, again['ETag']), (304, b'', r['ETag']))
        Property.objects.filter(pk=p.pk).update(price=1)
        self.assertEqual(self.get(p, HTTP_IF_NONE_MATCH=r['ETag']).status_code, 200)

    def test_search_etag_is_stable(self):
        self.make()
        self.assertEqual(self.get()['ETag'], self.get()['ETag'])

    def test_errors_are_not_marked_cacheable(self):
        self.assertNotIn('public', self.get('nope').get('Cache-Control', ''))

    def test_read_only(self):
        p = self.make()
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.api, method)(f'{URL}{p.pk}/').status_code, 405)

    def test_throttled(self):
        p = self.make()
        with patch('apps.properties.public_views.PublicListingThrottle.get_rate', return_value='1/min'):
            self.assertEqual([self.get(p).status_code, self.get(p).status_code], [200, 429])


class SearchTest(Base):
    def search(self, **q):
        return self.api.get(URL + 'search/', q)

    def test_same_engine_as_the_search_api(self):
        for i in range(4):
            self.make(f'House {i}', price=5_000_000 + i, bedrooms=i + 1)
        pub = self.search(location='Lahore', purpose='sale', max_price='9000000', min_bedrooms='2', utm_source='x')
        rest = self.api.post('/api/v1/search/', {'location': {'free_text': 'Lahore'}, 'purpose': 'sale',
                                                 'price': {'max': 9_000_000}, 'bedrooms': {'min': 2}}, format='json')
        self.assertEqual([r['listing']['id'] for r in pub.data['results']],
                         [r['listing']['id'] for r in rest.data['results']])
        self.assertEqual(pub.data['total'], 3)

    def test_every_parameter_is_accepted(self):
        self.make()
        q = {'market': 'pk', 'purpose': 'sale', 'category': 'residential', 'subtypes': 'house,villa', 'location': 'Lahore',
             'min_price': '1', 'max_price': '99', 'currency': 'PKR', 'price_period': 'total', 'min_bedrooms': '1',
             'max_bedrooms': '9', 'min_bathrooms': '1', 'min_size': '1', 'max_size': '2.5', 'size_unit': 'marla',
             'amenities': 'gym', 'accessibility': 'wheelchair_access', 'furnishing': 'furnished', 'completion': 'ready',
             'pets_allowed': 'true', 'installments': '0', 'verified_only': 'true', 'listed_within_days': '7',
             'q': 'corner', 'sort': 'newest', 'limit': '5'}
        self.assertEqual(self.search(**q).status_code, 200)

    def test_bad_parameters(self):
        for name, value in (('max_price', '1e9'), ('max_price', '-1'), ('max_price', '９'), ('min_size', 'x'),
                            ('max_size', 'nan'), ('pets_allowed', 'maybe'), ('sort', 'x'), ('limit', '0'),
                            ('limit', '51'), ('cursor', 'x'), ('location_ids', '1,a'), ('market', 'ZZ')):
            self.assertEqual(self.search(**{name: value}).status_code, 400, (name, value))

    def test_paging(self):
        for i in range(5):
            self.make(f'House {i}', price=100 + i)
        seen, cursor = [], ''
        while cursor is not None:
            r = self.search(limit='2', cursor=cursor, sort='price_asc').data
            seen += [x['listing']['price'] for x in r['results']]
            cursor = r['next_cursor']
        self.assertEqual(seen, [100, 101, 102, 103, 104])

    def test_query_count_does_not_grow(self):
        self.search()          # warm the market cache
        counts = []
        for n in (2, 5):
            for i in range(n):
                p = self.make(f'{n} {i}', price=100 * n + i)
                PropertyImage.objects.create(property=p, image='a.jpg', sha256='x' * 64)
            with CaptureQueriesContext(connection) as q:
                self.search()
            counts.append(len(q))
        self.assertEqual(counts[0], counts[1])


class RedactionTest(SimpleTestCase):
    def test_contacts(self):
        for text in ('0300 1234567', '+92-300-1234567', '00923001234567', '(042) 111 222 333', 'a.b@c.co', '۰۳۰۰۱۲۳۴۵۶۷'):
            self.assertEqual(P.redact_contacts(f'call {text} now'), 'call [contact hidden] now', text)
        for text in ('PKR 25,000,000', '25000000', '1,200 sqft in 2015', '10 marla, 5 bed, 4 bath', 'Phase 5 Block 12'):
            self.assertEqual(P.redact_contacts(text), text)
