"""
Tests for feature_property_audit:
  - Organisation brand_color field
  - PropertyAudit delivery fields + AuditDeliveryFailure model
  - WhatsAppClient.upload_media / send_document
  - AuditMediaGateway
  - send_audit_via_whatsapp_task (success + failure paths)
  - 30-concurrent PDF generation load test
"""
from unittest.mock import Mock, patch

from django.test import TestCase

from apps.organizations.models import Organization


class TestOrganizationBrandColor(TestCase):

    def test_brand_color_defaults_to_platform_blue(self):
        org = Organization.objects.create(name='Branding Test Org')
        self.assertEqual(org.brand_color, '#1B4F72')

    def test_brand_color_can_be_set(self):
        org = Organization.objects.create(name='Custom Color Org', brand_color='#FF5733')
        org.refresh_from_db()
        self.assertEqual(org.brand_color, '#FF5733')

    def test_brand_color_rejects_invalid_format(self):
        from django.core.exceptions import ValidationError
        org = Organization(name='Bad Color Org', brand_color='red')
        with self.assertRaises(ValidationError):
            org.full_clean()


from apps.audit.models import AuditDeliveryFailure, PropertyAudit


class TestAuditDeliveryModel(TestCase):

    def _make_audit(self, **kwargs):
        defaults = dict(
            city='lahore', location='dha', property_type='house',
            estimated_value=5_000_000, currency='PKR',
        )
        defaults.update(kwargs)
        return PropertyAudit.objects.create(**defaults)

    def test_delivery_status_defaults_to_pending(self):
        audit = self._make_audit()
        self.assertEqual(audit.delivery_status, 'pending')

    def test_cloudinary_url_defaults_blank(self):
        audit = self._make_audit()
        self.assertEqual(audit.cloudinary_url, '')

    def test_audit_delivery_failure_creation(self):
        audit = self._make_audit()
        failure = AuditDeliveryFailure.objects.create(
            audit=audit,
            failure_type='wa_upload',
            error_detail='Connection timeout',
        )
        self.assertEqual(failure.audit_id, audit.pk)
        self.assertEqual(failure.failure_type, 'wa_upload')

    def test_audit_delivery_failure_ordering(self):
        audit = self._make_audit()
        AuditDeliveryFailure.objects.create(audit=audit, failure_type='pdf_gen')
        AuditDeliveryFailure.objects.create(audit=audit, failure_type='wa_send')
        failures = list(AuditDeliveryFailure.objects.filter(audit=audit))
        # Newest first (ordering = ['-created_at'])
        self.assertEqual(failures[0].failure_type, 'wa_send')


import requests as _requests

from apps.whatsapp.client import WhatsAppClient


class TestWhatsAppClientAuditMethods(TestCase):

    def setUp(self):
        from apps.resilience.resilience_engine import meta_cloud_api_circuit
        meta_cloud_api_circuit.reset()

    def tearDown(self):
        from apps.resilience.resilience_engine import meta_cloud_api_circuit
        meta_cloud_api_circuit.reset()

    @patch('apps.whatsapp.client.requests.post')
    def test_upload_media_returns_media_id(self, mock_post):
        mock_post.return_value.json.return_value = {'id': 'doc_abc123'}
        mock_post.return_value.raise_for_status = Mock()
        client = WhatsAppClient('test_token', 'phone_id_1')
        result = client.upload_media(b'%PDF-1.4 fake content', filename='audit.pdf')
        self.assertEqual(result, 'doc_abc123')
        call_url = mock_post.call_args[0][0]
        self.assertIn('/phone_id_1/media', call_url)
        call_files = mock_post.call_args[1]['files']
        self.assertIn('file', call_files)

    @patch('apps.whatsapp.client.requests.post')
    def test_upload_media_raises_on_api_error(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = _requests.HTTPError('400')
        client = WhatsAppClient('test_token', 'phone_id_1')
        with self.assertRaises(_requests.HTTPError):
            client.upload_media(b'pdf', filename='audit.pdf')

    @patch('apps.whatsapp.client.requests.post')
    @patch('apps.whatsapp.client.is_within_24h_window', return_value=True)
    def test_send_document_correct_payload(self, _mock_window, mock_post):
        mock_post.return_value.json.return_value = {'messages': [{'id': 'msg_1'}]}
        mock_post.return_value.raise_for_status = Mock()
        client = WhatsAppClient('test_token', 'phone_id_1')
        result = client.send_document(
            '+923001234567', 'doc_abc123',
            filename='PropertyAudit.pdf', caption='Your report',
        )
        self.assertEqual(result['messages'][0]['id'], 'msg_1')
        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['type'], 'document')
        self.assertEqual(payload['document']['id'], 'doc_abc123')
        self.assertEqual(payload['document']['filename'], 'PropertyAudit.pdf')
        self.assertEqual(payload['to'], '923001234567')

    @patch('apps.whatsapp.client.is_within_24h_window', return_value=False)
    def test_send_document_raises_outside_window(self, _mock_window):
        client = WhatsAppClient('test_token', 'phone_id_1')
        with self.assertRaises(ValueError):
            client.send_document('+923001234567', 'doc_abc123')


class TestPDFBranding(TestCase):

    def _sample_audit_data(self):
        from apps.audit.services import AuditEngine
        return AuditEngine.run(
            city='lahore', location='dha', property_type='house',
            estimated_value=5_000_000, value_currency='PKR', area_marla=10,
        )

    def test_generate_audit_pdf_bytes_returns_pdf(self):
        from apps.audit.pdf import generate_audit_pdf_bytes
        pdf = generate_audit_pdf_bytes(self._sample_audit_data())
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf.startswith(b'%PDF'), "Output is not a PDF")
        self.assertGreater(len(pdf), 1024)

    def test_generate_audit_pdf_bytes_with_brand_context(self):
        from apps.audit.pdf import OrgBrandContext, generate_audit_pdf_bytes
        ctx = OrgBrandContext(org_name='Acme Realty', brand_color='#FF5733', logo_url=None)
        pdf = generate_audit_pdf_bytes(self._sample_audit_data(), brand_context=ctx)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1024)

    def test_brand_context_defaults(self):
        from apps.audit.pdf import OrgBrandContext
        ctx = OrgBrandContext(org_name='My Org')
        self.assertEqual(ctx.brand_color, '#1B4F72')
        self.assertIsNone(ctx.logo_url)
        self.assertEqual(ctx.measurement_system, 'pk_traditional')


from apps.audit.gateway import AuditMediaGateway


class TestAuditMediaGateway(TestCase):

    @patch('cloudinary.uploader.upload')
    def test_upload_to_cloudinary_returns_secure_url(self, mock_upload):
        expected_url = 'https://res.cloudinary.com/demo/raw/upload/audits/org1/abc/audit_42.pdf'
        mock_upload.return_value = {'secure_url': expected_url}
        url = AuditMediaGateway.upload_to_cloudinary(b'%PDF fake', audit_id=42, org_id='org1')
        self.assertEqual(url, expected_url)
        call_kwargs = mock_upload.call_args[1]
        self.assertEqual(call_kwargs['resource_type'], 'raw')
        self.assertTrue(call_kwargs['folder'].startswith('audits/org1/'))
        self.assertEqual(call_kwargs['public_id'], 'audit_42')

    @patch('apps.whatsapp.client.WhatsAppClient.upload_media', return_value='doc_meta_123')
    def test_register_with_meta_returns_document_id(self, mock_upload):
        org = Organization.objects.create(name='Meta Test Org')
        result = AuditMediaGateway.register_with_meta(b'%PDF fake', 'audit.pdf', org)
        self.assertEqual(result, 'doc_meta_123')

    @patch('apps.whatsapp.client.WhatsAppClient.send_document',
           return_value={'messages': [{'id': 'msg_999'}]})
    @patch('apps.whatsapp.client.is_within_24h_window', return_value=True)
    def test_dispatch_to_chat_returns_meta_response(self, _mock_win, mock_send):
        org = Organization.objects.create(name='Dispatch Test Org')
        result = AuditMediaGateway.dispatch_to_chat(
            '+923001234567', 'doc_meta_123', 'audit.pdf', org, caption='Report ready'
        )
        self.assertEqual(result['messages'][0]['id'], 'msg_999')

    @patch('apps.whatsapp.client.WhatsAppClient.send_text')
    @patch('apps.whatsapp.client.is_within_24h_window', return_value=True)
    def test_send_fallback_link_calls_send_text(self, _mock_win, mock_send_text):
        org = Organization.objects.create(name='Fallback Test Org')
        AuditMediaGateway.send_fallback_link('+923001234567', 'https://cdn.example.com/audit.pdf', org)
        mock_send_text.assert_called_once()
        call_args = mock_send_text.call_args
        self.assertIn('https://cdn.example.com/audit.pdf', call_args[0][1])


from apps.audit.tasks import send_audit_via_whatsapp_task


class TestSendAuditViaWhatsApp(TestCase):

    def setUp(self):
        self.org = Organization.objects.create(name='Task Test Org')
        self.audit = PropertyAudit.objects.create(
            city='lahore', location='dha', property_type='house',
            estimated_value=5_000_000, currency='PKR',
            phone='+923001234567',
            delivery_status='ready',
            cloudinary_url='https://res.cloudinary.com/demo/raw/upload/audit_42.pdf',
        )

    @patch('urllib.request.urlopen')
    @patch('apps.audit.gateway.AuditMediaGateway.register_with_meta', return_value='doc_123')
    @patch('apps.audit.gateway.AuditMediaGateway.dispatch_to_chat',
           return_value={'messages': [{'id': 'msg_1'}]})
    def test_successful_delivery_sets_status_sent(self, mock_dispatch, mock_register, mock_urlopen):
        mock_urlopen.return_value.read.return_value = b'%PDF fake content'
        send_audit_via_whatsapp_task(self.audit.pk, '+923001234567', str(self.org.pk))
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.delivery_status, 'sent')
        self.assertEqual(AuditDeliveryFailure.objects.count(), 0)
        mock_dispatch.assert_called_once()

    @patch('urllib.request.urlopen')
    @patch('apps.audit.gateway.AuditMediaGateway.register_with_meta',
           side_effect=Exception('Connection timeout'))
    @patch('apps.audit.gateway.AuditMediaGateway.send_fallback_link')
    def test_wa_upload_failure_logs_and_sends_fallback(self, mock_fallback, mock_register, mock_urlopen):
        mock_urlopen.return_value.read.return_value = b'%PDF fake content'
        send_audit_via_whatsapp_task(self.audit.pk, '+923001234567', str(self.org.pk))
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.delivery_status, 'failed')
        self.assertEqual(
            AuditDeliveryFailure.objects.filter(failure_type='wa_upload').count(), 1
        )
        mock_fallback.assert_called_once_with(
            '+923001234567',
            'https://res.cloudinary.com/demo/raw/upload/audit_42.pdf',
            self.org,
        )

    @patch('urllib.request.urlopen')
    @patch('apps.audit.gateway.AuditMediaGateway.register_with_meta', return_value='doc_456')
    @patch('apps.audit.gateway.AuditMediaGateway.dispatch_to_chat',
           side_effect=Exception('403 Bad Request'))
    @patch('apps.audit.gateway.AuditMediaGateway.send_fallback_link')
    def test_wa_send_failure_after_upload_logs_correct_type(
        self, mock_fallback, mock_dispatch, mock_register, mock_urlopen
    ):
        mock_urlopen.return_value.read.return_value = b'%PDF fake content'
        send_audit_via_whatsapp_task(self.audit.pk, '+923001234567', str(self.org.pk))
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.delivery_status, 'failed')
        self.assertEqual(
            AuditDeliveryFailure.objects.filter(failure_type='wa_send').count(), 1
        )
        mock_fallback.assert_called_once()


class TestAuditPDFConcurrency(TestCase):
    """30 concurrent PDF generations. Asserts no errors, all PDFs non-empty, memory delta < 50 MB."""

    def test_30_concurrent_pdf_generations(self):
        import tracemalloc
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from apps.audit.pdf import OrgBrandContext, generate_audit_pdf_bytes
        from apps.audit.services import AuditEngine

        audit_data = AuditEngine.run(
            city='lahore', location='dha', property_type='house',
            estimated_value=5_000_000, value_currency='PKR', area_marla=10,
        )
        brand = OrgBrandContext(org_name='Load Test Org', brand_color='#1B4F72', logo_url=None)

        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        errors: list = []
        pdf_sizes: list = []

        def _generate_one(_):
            try:
                pdf = generate_audit_pdf_bytes(audit_data, brand_context=brand)
                return len(pdf)
            except Exception as exc:
                errors.append(exc)
                return 0

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_generate_one, i) for i in range(30)]
            for fut in as_completed(futures):
                pdf_sizes.append(fut.result())

        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        self.assertEqual(errors, [], f"PDF generation errors: {errors}")
        self.assertTrue(all(s > 0 for s in pdf_sizes), "One or more PDFs were empty")
        self.assertEqual(len(pdf_sizes), 30)

        stats = snapshot_after.compare_to(snapshot_before, 'lineno')
        delta_kb = sum(s.size_diff for s in stats if s.size_diff > 0) / 1024
        self.assertLess(
            delta_kb, 50_000,
            f"Memory delta too high: {delta_kb:.0f} KB — possible leak"
        )
