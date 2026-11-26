"""T-4050 — persistent duplicate clustering and fraud signals v2 (FR-LST-010, ADR-069)."""
import io
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image, ImageDraw
from rest_framework.test import APIClient

from apps.core.images import dhash, hamming
from apps.moderation import checks
from apps.moderation.models import ModerationItem
from apps.properties import duplicates as D
from apps.properties.models import DuplicateCluster, Property, PropertyImage
from tests.factories import make_developer, make_org, make_property, make_user
from tests.test_runtime import LOCMEM

URL = '/api/v1/moderation/duplicates/'


def picture(seed=0, size=(240, 180), quality=None):
    img = Image.new('RGB', size, (200, 200, 200))
    d = ImageDraw.Draw(img)
    for i in range(6):
        x = (seed * 37 + i * 41) % size[0]
        d.rectangle([x, i * 25, x + 60, i * 25 + 40], fill=((seed * 50 + i * 30) % 255, 90, 160))
    if quality:                                     # recompressed and resized copy
        buf = io.BytesIO()
        img.resize((size[0] // 2, size[1] // 2)).save(buf, 'JPEG', quality=quality)
        img = Image.open(io.BytesIO(buf.getvalue()))
    return img


def photo(p, img, sha=None):
    return PropertyImage.objects.create(property=p, image='property_images/x.jpg', sha256=sha or f'{p.pk.hex}{id(img)}'[:64],
                                        dhash=dhash(img))


@override_settings(CACHES=LOCMEM)
class Base(TestCase):
    def setUp(self):
        cache.clear()
        _, self.org = make_developer(org=make_org(name='Prime'))
        _, self.other = make_developer(org=make_org(name='Copycat'))

    def listing(self, org=None, **kw):
        kw = {'title': 'House', 'city': 'Lahore', 'location': 'DHA Phase 5', 'price': 30_000_000,
              'size_value': 200, 'size_unit': 'sqm', 'bedrooms': 4, 'country': 'PK', 'currency': 'PKR', **kw}
        p = make_property(org=org or self.org, **kw)
        Property.objects.filter(pk=p.pk).update(is_sandbox=False)
        p.refresh_from_db()
        return p


class HashTest(TestCase):
    def test_dhash_survives_resize_and_recompression(self):
        a, b, c = picture(1), picture(1, quality=40), picture(7)
        self.assertLessEqual(hamming(dhash(a), dhash(b)), D.MAX_HAMMING)
        self.assertGreater(hamming(dhash(a), dhash(c)), D.MAX_HAMMING)


class ClusterTest(Base):
    def test_fingerprint_tolerance_and_cluster(self):
        a = self.listing()
        b = self.listing(org=self.other, title='Different words', price=31_000_000, size_value=205)
        far = self.listing(price=40_000_000)                                       # price beyond 5%
        rent = self.listing(purpose='rent_long', price_type='per_month', price=200_000)
        self.assertEqual({q.pk for q in D.fingerprint_matches(a)}, {b.pk})
        c = D.refresh(a)
        self.assertEqual(set(c.listings.values_list('pk', flat=True)), {a.pk, b.pk})
        self.assertEqual((c.canonical_id, c.evidence), (a.pk, {'fingerprint': 1, 'image': 0}))
        self.assertIsNone(D.refresh(far))
        self.assertIsNone(D.refresh(rent))
        Property.objects.filter(pk=b.pk).update(price=50_000_000)                   # b changes its terms
        self.assertIsNone(D.refresh(b))
        self.assertFalse(DuplicateCluster.objects.exists())                          # a cluster of one dissolves

    def test_photo_links_listings_and_clusters_merge(self):
        a = self.listing()
        b = self.listing(org=self.other, location='Gulberg', price=1_000)            # no fingerprint match
        c = self.listing(price=30_500_000)                                            # fingerprint of a
        photo(a, picture(3))
        photo(b, picture(3, quality=50))
        first = D.refresh(b)
        self.assertEqual(set(first.listings.values_list('pk', flat=True)), {a.pk, b.pk})
        merged = D.refresh(c)
        self.assertEqual(merged.pk, first.pk)
        self.assertEqual(Property.objects.filter(duplicate_cluster=merged).count(), 3)

    def test_signal_path_and_rebuild_command(self):
        a = self.listing()
        with self.captureOnCommitCallbacks(execute=True):
            b = self.listing(org=self.other)
        b.refresh_from_db()
        self.assertIsNotNone(b.duplicate_cluster_id)
        self.assertTrue(b.duplicate_key)
        img = photo(a, picture(2))
        PropertyImage.objects.filter(pk=img.pk).update(dhash='')
        out = StringIO()
        with self.settings(MEDIA_ROOT='/tmp/none'):
            call_command('rebuild_duplicate_clusters', stdout=out, stderr=StringIO())
        self.assertIn('hashed=0 failed=1', out.getvalue())                          # file missing: reported
        self.assertEqual(Property.objects.filter(duplicate_cluster__isnull=False).count(), 2)


class SignalTest(Base):
    def test_image_reused_from_another_lister(self):
        a = self.listing()
        photo(a, picture(5))
        copy = self.listing(org=self.other, location='Bahria Town', price=9_000_000)
        photo(copy, picture(5, quality=35))
        self.assertIn('image_reused', checks.run(copy))
        own1 = self.listing(location='Johar Town', price=8_000_000)                  # same lister reusing is fine
        own2 = self.listing(location='Model Town', price=7_000_000)
        photo(own1, picture(9))
        photo(own2, picture(9))
        self.assertNotIn('image_reused', D.signals(own2))

    def test_reposted_after_takedown(self):
        old = self.listing()
        Property.objects.filter(pk=old.pk).update(status='suspended')
        again = self.listing(org=self.other, title='Fresh title')
        self.assertIn('reposted_after_takedown', checks.run(again))
        fine = self.listing(location='Elsewhere')
        self.assertNotIn('reposted_after_takedown', D.signals(fine))
        ModerationItem.objects.create(listing=fine, reason='review', status='rejected', due_at=fine.created_at)
        twin = self.listing(org=self.other, location='Elsewhere')
        self.assertIn('reposted_after_takedown', D.signals(twin))


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        self.mod = make_user(role='admin', platform_role='ops_admin')
        self.a, self.b = self.listing(), self.listing(org=self.other)
        self.c = D.refresh(self.a)

    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_moderators_list_confirm_and_split(self):
        self.assertEqual(self.api(make_user(role='admin', platform_role='support_admin')).get(URL).status_code, 403)
        r = self.api(self.mod).get(URL)
        self.assertEqual(r.status_code, 200)
        row = r.data['results'][0]
        self.assertEqual((len(row['listings']), row['distinct_listers']), (2, 2))
        url = URL + f'{self.c.pk}/action/'
        r = self.api(self.mod).post(url, {'action': 'confirm', 'canonical': str(self.b.pk)}, format='json')
        self.assertEqual((r.status_code, r.data['status'], r.data['canonical']), (200, 'confirmed', str(self.b.pk)))
        self.assertEqual(self.api(self.mod).post(url, {'action': 'confirm', 'canonical': str(self.listing(
            location='Nowhere').pk)}, format='json').status_code, 400)
        self.assertEqual(self.api(self.mod).post(url, {'action': 'nope'}, format='json').status_code, 400)
        r = self.api(self.mod).post(url, {'action': 'split', 'listing': str(self.b.pk)}, format='json')
        self.assertEqual((r.status_code, r.data), (200, None))                      # a cluster of one dissolves
        self.b.refresh_from_db()
        self.assertTrue(self.b.duplicate_distinct)
        self.assertIsNone(D.refresh(self.a))                                         # b never clustered again
