"""Owner decisions 2026-11-25: export for everyone (KI-085), Cloudinary files erased (KI-105),
scraping from a monitoring stack on another host (KI-109)."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.audit.models import PropertyAudit
from apps.compliance import cloud_files as CF
from apps.compliance.models import DataExportRequest
from apps.reports.models import Report
from apps.whatsapp.models import WhatsAppMessage, WhatsAppSession
from tests.factories import make_client
from tests.test_mvp_data_protection import PersonTest
from tests.test_runtime import LOCMEM

CLOUD = {'CLOUD_NAME': 'rt-cloud', 'API_KEY': 'k', 'API_SECRET': 's'}
REPORT_URL = 'https://res.cloudinary.com/rt-cloud/raw/upload/v1700000000/reports/abc.pdf'
AUDIT_URL = 'https://res.cloudinary.com/rt-cloud/raw/upload/v17/audits/org/0f1e/audit_7.pdf'
MEDIA_URL = 'https://res.cloudinary.com/rt-cloud/image/upload/v5/wa-media/media-1.jpg'
TOKEN = 'a-long-enough-metrics-token'


# ── KI-085: every signed-in person may export ─────────────────────────────────

@override_settings(CACHES=LOCMEM)
class ExportForEveryoneTest(TestCase):
    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_consumer_in_a_non_gdpr_market(self):
        user = make_client(phone='+923215550123')
        with patch('apps.compliance.tasks.generate_data_export.delay') as delay, \
                self.captureOnCommitCallbacks(execute=True):
            r = self.api(user).post('/api/v1/compliance/export/')
        self.assertEqual(r.status_code, 202)
        delay.assert_called_once_with(r.data['id'])
        self.assertEqual(DataExportRequest.objects.get().user, user)

    def test_repeated_requests_queue_one_export(self):
        user, other = make_client(), make_client()
        with patch('apps.compliance.tasks.generate_data_export.delay') as delay, \
                self.captureOnCommitCallbacks(execute=True):
            first = self.api(user).post('/api/v1/compliance/export/')
            again = self.api(user).get('/api/v1/compliance/export/')        # legacy verb
            theirs = self.api(other).post('/api/v1/compliance/export/')
        self.assertEqual((first.status_code, again.status_code, theirs.status_code), (202, 200, 202))
        self.assertEqual(again.data['id'], first.data['id'])
        self.assertNotEqual(theirs.data['id'], first.data['id'])
        self.assertEqual(delay.call_count, 2)
        DataExportRequest.objects.filter(pk=first.data['id']).update(status='ready')
        with patch('apps.compliance.tasks.generate_data_export.delay'):
            self.assertEqual(self.api(user).post('/api/v1/compliance/export/').status_code, 202)

    def test_anonymous_is_refused(self):
        self.assertEqual(APIClient().post('/api/v1/compliance/export/').status_code, 401)

    def test_deletion_keeps_its_gate(self):
        """Only export was decided; self-service deletion outside GDPR markets is unchanged."""
        user = make_client(phone='+923215550124')
        self.assertEqual(self.api(user).delete('/api/v1/compliance/my-data/').status_code, 404)


# ── KI-105: Cloudinary files ──────────────────────────────────────────────────

class ParseTest(TestCase):
    @override_settings(CLOUDINARY_STORAGE=CLOUD)
    def test_public_ids(self):
        self.assertEqual(CF.parse(REPORT_URL), ('raw', 'reports/abc.pdf'))
        self.assertEqual(CF.parse(MEDIA_URL), ('image', 'wa-media/media-1.jpg'))
        self.assertEqual(CF.parse('https://res.cloudinary.com/rt-cloud/image/upload/c_fill,w_100/v5/a/b.png'),
                         ('image', 'a/b.png'))
        self.assertEqual(CF.parse('https://res.cloudinary.com/rt-cloud/raw/upload/reports/x'), ('raw', 'reports/x'))
        for url in ('', 'not a url', 'https://example.com/raw/upload/v1/x.pdf',
                    'https://res.cloudinary.com/other-cloud/raw/upload/v1/x.pdf',
                    'https://res.cloudinary.com/rt-cloud/raw/fetch/v1/x.pdf', '/media/audits/x.pdf'):
            self.assertIsNone(CF.parse(url), url)

    def test_candidates(self):
        self.assertEqual(CF._candidates('raw', 'reports/abc.pdf'), ['reports/abc.pdf', 'reports/abc'])
        self.assertEqual(CF._candidates('image', 'wa/m.jpg'), ['wa/m', 'wa/m.jpg'])
        self.assertEqual(CF._candidates('raw', 'a.b/c'), ['a.b/c'])


@override_settings(CLOUDINARY_STORAGE=CLOUD)
class DeleteTest(TestCase):
    def test_ok_not_found_and_fallback(self):
        with patch('cloudinary.uploader.destroy', side_effect=[{'result': 'not found'}, {'result': 'ok'}]) as d:
            self.assertTrue(CF.delete(REPORT_URL))
        self.assertEqual([c.args[0] for c in d.call_args_list], ['reports/abc.pdf', 'reports/abc'])
        self.assertEqual(d.call_args.kwargs['resource_type'], 'raw')
        with patch('cloudinary.uploader.destroy', return_value={'result': 'not found'}):
            self.assertTrue(CF.delete(REPORT_URL))                      # already gone counts as done
        with patch('cloudinary.uploader.destroy') as d:
            self.assertFalse(CF.delete('https://example.com/x.pdf'))     # not ours: nothing to do
        d.assert_not_called()

    def test_errors_raise_without_the_url(self):
        with patch('cloudinary.uploader.destroy', side_effect=ConnectionError(REPORT_URL)):
            with self.assertRaises(CF.CloudFileError) as ctx:
                CF.delete(REPORT_URL)
        self.assertNotIn('reports', str(ctx.exception))
        with patch('cloudinary.uploader.destroy', return_value={'result': 'error'}), \
                self.assertRaises(CF.CloudFileError):
            CF.delete(REPORT_URL)
        with override_settings(CLOUDINARY_STORAGE={'CLOUD_NAME': 'rt-cloud', 'API_KEY': '', 'API_SECRET': ''}), \
                self.assertRaises(CF.CloudFileError):
            CF.delete(REPORT_URL)


@override_settings(CLOUDINARY_STORAGE=CLOUD)
class ErasureDeletesFilesTest(PersonTest):
    def files(self):
        Report.objects.create(user=self.user, report_type='fraud_check', content={}, file_url=REPORT_URL)
        PropertyAudit.objects.create(user=self.user, phone=self.phone, city='Lahore', location='DHA',
                                     property_type='house', estimated_value=1, cloudinary_url=AUDIT_URL)
        s = WhatsAppSession.objects.create(phone=self.phone.lstrip('+'))
        WhatsAppMessage.objects.create(session=s, wa_message_id='m-media', direction='inbound', body='',
                                       media_url=MEDIA_URL)

    def test_files_are_deleted_before_the_rows(self):
        self.files()
        with patch('cloudinary.uploader.destroy', return_value={'result': 'ok'}) as d:
            req = self.run_erasure()
        self.assertEqual(req.status, 'completed')
        self.assertEqual(req.erasure_scope['cloud_files_deleted'], 3)
        self.assertEqual(sorted(c.args[0] for c in d.call_args_list),
                         ['audits/org/0f1e/audit_7.pdf', 'reports/abc.pdf', 'wa-media/media-1'])
        self.assertEqual(list(PropertyAudit.objects.values_list('cloudinary_url', 'user')), [('', None)])

    def test_a_failed_delete_keeps_the_request_open(self):
        self.files()
        with patch('cloudinary.uploader.destroy', side_effect=ConnectionError('down')):
            req = self.run_erasure()
        self.assertEqual((req.status, req.erasure_scope['errors']), ('failed', {'cloud_files_deleted': 'CloudFileError'}))
        self.assertEqual((self.user.phone, Report.objects.get().file_url), (self.phone, REPORT_URL))
        with patch('cloudinary.uploader.destroy', return_value={'result': 'ok'}):
            req = self.run_erasure()
        self.assertEqual(req.status, 'completed')


# ── KI-109: a monitoring stack elsewhere ─────────────────────────────────────

@override_settings(CACHES=LOCMEM, PROMETHEUS_ALLOWED_IPS=['127.0.0.1'], PROMETHEUS_METRICS_TOKEN=TOKEN,
                   ALLOWED_HOSTS=['*'])
class RemoteScrapeTest(TestCase):
    def get(self, **kw):
        return self.client.get('/metrics/', REMOTE_ADDR='203.0.113.7', **kw)

    def test_bearer_token(self):
        self.assertEqual(self.get().status_code, 403)
        self.assertEqual(self.get(HTTP_AUTHORIZATION='Bearer wrong-token-of-length').status_code, 403)
        self.assertEqual(self.get(HTTP_AUTHORIZATION=f'Basic {TOKEN}').status_code, 403)
        self.assertEqual(self.get(HTTP_AUTHORIZATION=f'Bearer {TOKEN}').status_code, 200)

    def test_short_token_is_off(self):
        with override_settings(PROMETHEUS_METRICS_TOKEN='short'):
            self.assertEqual(self.get(HTTP_AUTHORIZATION='Bearer short').status_code, 403)

    def test_internal_host_names_reach_operational_paths(self):
        """Found by the stack run: the tenant middleware answered 404 to Host: web:8000."""
        r = self.get(HTTP_HOST='web:8000', HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(self.client.get('/health/', HTTP_HOST='web:8000').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/theme/', HTTP_HOST='unknown.example.org').status_code, 404)
