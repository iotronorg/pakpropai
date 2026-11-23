"""
T-1110 — image upload hardening (ADR-027).

The sanitiser (magic-byte sniffing, pixel budget / decompression bombs,
truncated and polyglot files, EXIF/GPS and text-chunk stripping with the
orientation applied, downscaling, safe names), the listing image API
(all-or-nothing, duplicates, limits, auth, alt text, reorder, delete), the
logo / agent-photo field and the backfill command.
"""
import io
import shutil
import struct
import tempfile
import zlib
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image, PngImagePlugin
from rest_framework.test import APIClient

from apps.core.images import ImageRejected, SanitizedImageField, safe_upload_name, sanitize_image
from apps.properties.models import PropertyImage
from tests.factories import make_developer, make_property, make_user

URL = '/api/v1/properties/'
GPS_TAG, ORIENTATION_TAG = 0x8825, 0x0112


def _jpeg(w=64, h=32, *, gps=True, orientation=None, color=(200, 30, 30), tail=b''):
    img = Image.new('RGB', (w, h), color)
    exif = Image.Exif()
    exif[0x010F] = 'SecretCam'                                  # Make
    if gps:
        exif[GPS_TAG] = {1: 'N', 2: (31.0, 31.0, 0.0), 3: 'E', 4: (74.0, 21.0, 0.0)}
    if orientation:
        exif[ORIENTATION_TAG] = orientation
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif.tobytes(), comment=b'secret comment')
    return buf.getvalue() + tail


def _png(w=40, h=40, *, alpha=True):
    img = Image.new('RGBA' if alpha else 'RGB', (w, h), (0, 128, 0, 100) if alpha else (0, 128, 0))
    info = PngImagePlugin.PngInfo()
    info.add_text('Comment', 'secret text chunk')
    buf = io.BytesIO()
    img.save(buf, 'PNG', pnginfo=info)
    return buf.getvalue()


def _upload(data, name='photo.jpg', ctype='image/jpeg'):
    return SimpleUploadedFile(name, data, content_type=ctype)


def _forged_png_header(w, h):
    """A tiny PNG whose IHDR claims ``w × h`` (valid CRC): a decompression bomb's header."""
    raw = _png(1, 1, alpha=False)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    chunk = b'IHDR' + ihdr
    return raw[:8] + struct.pack('>I', 13) + chunk + struct.pack('>I', zlib.crc32(chunk)) + raw[33:]


def _opened(content):
    return Image.open(io.BytesIO(content.read() if hasattr(content, 'read') else content))


class SanitizerTest(TestCase):
    def test_strips_metadata_and_applies_orientation(self):
        clean = sanitize_image(_upload(_jpeg(64, 32, orientation=6)))
        body = clean.content.read()
        out = Image.open(io.BytesIO(body))
        self.assertEqual((clean.format, out.size), ('JPEG', (32, 64)))      # rotated by EXIF 6
        self.assertFalse(out.getexif())                                     # no EXIF at all
        self.assertNotIn(b'SecretCam', body)
        self.assertNotIn(b'secret comment', body)
        self.assertRegex(clean.content.name, r'^[0-9a-f]{32}\.jpg$')

    def test_png_text_chunks_gone_alpha_kept_and_webp(self):
        clean = sanitize_image(_upload(_png(), 'x.png', 'image/png'))
        body = clean.content.read()
        self.assertNotIn(b'secret text chunk', body)
        self.assertEqual(Image.open(io.BytesIO(body)).mode, 'RGBA')
        buf = io.BytesIO()
        Image.new('RGB', (10, 10)).save(buf, 'WEBP')
        self.assertEqual(sanitize_image(_upload(buf.getvalue(), 'x.webp', 'image/webp')).format, 'WEBP')

    def test_format_from_bytes_not_from_client(self):
        clean = sanitize_image(_upload(_png(alpha=False), 'evil.jpg', 'image/jpeg'))
        self.assertEqual(clean.format, 'PNG')
        self.assertTrue(clean.content.name.endswith('.png'))
        for data, name, ctype in [
            (b'<html><script>alert(1)</script></html>', 'x.jpg', 'image/jpeg'),
            (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', 'x.svg', 'image/svg+xml'),
            (b'GIF89a' + b'\x00' * 30, 'x.gif', 'image/gif'),
            (b'%PDF-1.4 ...', 'x.png', 'image/png'),
        ]:
            with self.subTest(name=name), self.assertRaises(ImageRejected):
                sanitize_image(_upload(data, name, ctype))

    def test_polyglot_payload_dropped(self):
        clean = sanitize_image(_upload(_jpeg(tail=b'<script>alert(document.cookie)</script>')))
        self.assertNotIn(b'<script>', clean.content.read())

    def test_bombs_truncation_and_size(self):
        with self.assertRaisesMessage(ImageRejected, 'dimensions'):
            sanitize_image(_upload(_forged_png_header(100_000, 100_000), 'b.png', 'image/png'))
        with override_settings(IMAGE_MAX_PIXELS=1000), self.assertRaisesMessage(ImageRejected, 'dimensions'):
            sanitize_image(_upload(_jpeg(64, 32)))
        data = _jpeg(256, 256, gps=False, color=(1, 2, 3))
        with self.assertRaisesMessage(ImageRejected, 'not a valid image'):
            sanitize_image(_upload(data[: len(data) // 2]))
        with self.assertRaisesMessage(ImageRejected, 'too large'):
            sanitize_image(_upload(_jpeg()), max_bytes=100)
        with self.assertRaisesMessage(ImageRejected, 'Empty'):
            sanitize_image(_upload(b''))

    def test_downscale_and_thumbnail(self):
        clean = sanitize_image(_upload(_jpeg(3000, 1500, gps=False)), max_side=1000, thumbnail_side=100)
        self.assertEqual((clean.width, clean.height), (1000, 500))
        self.assertEqual(_opened(clean.thumbnail).size, (100, 50))
        self.assertTrue(clean.thumbnail.name.endswith('_t.jpg'))

    def test_safe_upload_name_and_drf_field(self):
        self.assertTrue(safe_upload_name('p/1', 'shell.html').endswith('.bin'))
        self.assertTrue(safe_upload_name('p/1', 'a.PNG').endswith('.png'))
        field = SanitizedImageField(max_side=16)
        out = field.run_validation(_upload(_jpeg(64, 32)))
        self.assertEqual(_opened(out).size, (16, 8))
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            field.run_validation(_upload(b'<html>', 'x.jpg'))


class _MediaTest(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)


class ImageApiTest(_MediaTest):
    def setUp(self):
        super().setUp()
        self.dev, self.org = make_developer()
        self.prop = make_property(org=self.org, owner=self.dev)
        self.c = APIClient()
        self.c.force_authenticate(self.dev)
        self.url = f'{URL}{self.prop.pk}/upload-images/'

    def _post(self, *files, client=None, **extra):
        return (client or self.c).post(self.url, {'images': list(files), **extra}, format='multipart')

    def test_upload_stores_clean_files(self):
        r = self._post(_upload(_jpeg(), '../../etc/passwd.html'), _upload(_png(), 'b.png', 'image/png'),
                       alt_text='Front of the house')
        self.assertEqual(r.status_code, 201, r.data)
        imgs = list(self.prop.images.order_by('order'))
        self.assertEqual([i.order for i in imgs], [0, 1])
        first = imgs[0]
        self.assertRegex(first.image.name, rf'^property_images/{self.prop.pk}/[0-9a-f]{{32}}\.jpg$')
        self.assertTrue(first.thumbnail.name.endswith('.jpg'))
        self.assertEqual((first.width, first.height, first.alt_text), (64, 32, 'Front of the house'))
        with first.image.open('rb') as fh:
            self.assertNotIn(b'SecretCam', fh.read())
        self.assertEqual(len(first.sha256), 64)
        self.assertIn('thumbnail_url', r.data[0])

    def test_all_or_nothing_duplicates_and_limits(self):
        r = self._post(_upload(_jpeg()), _upload(b'<html>', 'x.jpg'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('images[1]', r.data)
        self.assertNotIn('x.jpg', str(r.data))                               # no client names echoed
        self.assertEqual(self.prop.images.count(), 0)
        same = _jpeg()
        self.assertEqual(self._post(_upload(same), _upload(same)).status_code, 400)
        self.assertEqual(self._post(_upload(same)).status_code, 201)
        self.assertEqual(self._post(_upload(same)).status_code, 400)          # already on the listing
        with override_settings(MAX_IMAGES_PER_LISTING=2):
            r = self._post(_upload(_jpeg(color=(1, 1, 1))), _upload(_jpeg(color=(2, 2, 2))))
            self.assertEqual(r.status_code, 400)
        stranger = APIClient()
        stranger.force_authenticate(make_user(role='developer'))
        self.assertIn(self._post(_upload(_jpeg(color=(9, 9, 9))), client=stranger).status_code, (403, 404))

    def test_patch_reorder_delete(self):
        self._post(_upload(_jpeg(color=(1, 1, 1))), _upload(_jpeg(color=(2, 2, 2))))
        a, b = self.prop.images.order_by('order')
        r = self.c.patch(f'{URL}{self.prop.pk}/images/{a.pk}/', {'alt_text': 'Kitchen', 'width': 1},
                         format='json')
        self.assertEqual((r.status_code, r.data['alt_text'], r.data['width']), (200, 'Kitchen', 64))
        r = self.c.post(f'{URL}{self.prop.pk}/images/reorder/', {'ids': [str(b.pk), str(a.pk)]}, format='json')
        self.assertEqual([x['id'] for x in r.data], [str(b.pk), str(a.pk)])
        self.assertEqual(self.c.post(f'{URL}{self.prop.pk}/images/reorder/', {'ids': [str(a.pk)]},
                                     format='json').status_code, 400)
        storage, names = a.image.storage, (a.image.name, a.thumbnail.name)
        self.assertEqual(self.c.delete(f'{URL}{self.prop.pk}/images/{a.pk}/').status_code, 204)
        self.assertFalse(any(storage.exists(n) for n in names))


class BackfillTest(_MediaTest):
    def test_sanitize_images_command(self):
        dev, org = make_developer()
        prop = make_property(org=org, owner=dev)
        legacy = PropertyImage(property=prop)
        legacy.image.save('old.jpg', _upload(_jpeg()), save=True)               # pre-T-1110 row
        old_name = legacy.image.name
        out = StringIO()
        call_command('sanitize_images', '--dry-run', stdout=out)
        self.assertIn('1 property image(s)', out.getvalue())
        call_command('sanitize_images', stdout=out)
        legacy.refresh_from_db()
        self.assertEqual(len(legacy.sha256), 64)
        self.assertTrue(legacy.thumbnail)
        with legacy.image.open('rb') as fh:
            self.assertNotIn(b'SecretCam', fh.read())
        self.assertFalse(legacy.image.storage.exists(old_name))
        self.assertIn("'done': 1", out.getvalue())
