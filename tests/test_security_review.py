"""T-6030 — fixes from the security review pass.

Audit PDFs (bandit B310): the PDF worker used ``urllib.urlopen`` on the org logo URL, which also
accepts ``file://`` and internal addresses. The logo is now read through the storage backend and
the stored report is downloaded over https only, with size caps.
"""
import io
import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from apps.audit import tasks as audit_tasks
from apps.audit.pdf import OrgBrandContext, generate_audit_pdf_bytes
from apps.organizations.models import Organization


def png_bytes(side=40):
    buf = io.BytesIO()
    Image.new('RGB', (side, side), (200, 30, 30)).save(buf, format='PNG')
    return buf.getvalue()


def sample():
    from apps.audit.services import AuditEngine
    return AuditEngine.run(city='lahore', location='dha', property_type='house', estimated_value=5_000_000)


class LogoFromStorageTest(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)   # default storage is the file system in tests
        override.enable()
        self.addCleanup(override.disable)
        self.org = Organization.objects.create(name='Logo Org')

    def _set_logo(self, data):
        self.org.logo.save('logo.png', SimpleUploadedFile('logo.png', data), save=True)

    def test_logo_is_read_through_storage_and_never_fetched(self):
        self._set_logo(png_bytes())
        with patch('urllib.request.urlopen') as urlopen:
            data = audit_tasks._read_logo(self.org)
            pdf = generate_audit_pdf_bytes(sample(), brand_context=OrgBrandContext(
                org_name='Logo Org', logo_bytes=data, logo_url='http://169.254.169.254/latest/meta-data/'))
        urlopen.assert_not_called()
        self.assertTrue(data.startswith(b'\x89PNG'))
        self.assertIn(b'/Image', pdf)

    def test_oversized_or_missing_logo_is_skipped(self):
        self.assertIsNone(audit_tasks._read_logo(self.org))
        self._set_logo(png_bytes())
        with patch.object(audit_tasks, 'LOGO_MAX_BYTES', 10):
            self.assertIsNone(audit_tasks._read_logo(self.org))

    def test_a_logo_url_alone_is_ignored(self):
        with patch('urllib.request.urlopen') as urlopen:
            pdf = generate_audit_pdf_bytes(sample(), brand_context=OrgBrandContext(
                org_name='X', logo_url='file:///etc/passwd'))
        urlopen.assert_not_called()
        self.assertTrue(pdf.startswith(b'%PDF'))


class ReportDownloadTest(SimpleTestCase):
    def test_only_https(self):
        for url in ('http://res.cloudinary.com/a.pdf', 'file:///etc/passwd', 'ftp://x/a.pdf', '', None):
            with self.subTest(url=url), patch('urllib.request.urlopen') as urlopen:
                with self.assertRaises(ValueError):
                    audit_tasks._download_report(url)
                urlopen.assert_not_called()

    def test_size_cap(self):
        with patch('urllib.request.urlopen') as urlopen, patch.object(audit_tasks, 'REPORT_MAX_BYTES', 4):
            urlopen.return_value.read.return_value = b'%PDF-too-long'
            with self.assertRaises(ValueError):
                audit_tasks._download_report('https://res.cloudinary.com/a.pdf')
            urlopen.return_value.close.assert_called_once()

    def test_reads_the_report(self):
        with patch('urllib.request.urlopen') as urlopen:
            urlopen.return_value.read.return_value = b'%PDF ok'
            self.assertEqual(audit_tasks._download_report('https://res.cloudinary.com/a.pdf'), b'%PDF ok')
