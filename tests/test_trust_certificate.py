"""
Tests for the trust certificate feature.

Covers: PDF generator, Celery task (create / skip non-passed), trigger
in VerificationReviewView, TrustCertificateView RBAC, and org isolation.
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone

from apps.verification.models import Verification
from apps.verification.certificate import generate_trust_certificate

from tests.factories import make_developer, make_user, make_property, make_scan


def _make_passed_verification(org, dev):
    prop  = make_property(org=org)
    _, v  = make_scan(prop, dev)
    v.status      = Verification.Status.PASSED
    v.verified_at = timezone.now()
    v.signal_score = 85
    v.save()
    return prop, v


# ── Generator ─────────────────────────────────────────────────────────────────

class CertificateGeneratorTests(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()

    def test_generator_returns_bytes(self):
        prop, v = _make_passed_verification(self.org, self.dev)
        result = generate_trust_certificate(v)
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    def test_generator_starts_with_pdf_header(self):
        prop, v = _make_passed_verification(self.org, self.dev)
        result = generate_trust_certificate(v)
        self.assertTrue(result.startswith(b'%PDF'))

    def test_generator_with_qr_url(self):
        prop, v = _make_passed_verification(self.org, self.dev)
        result = generate_trust_certificate(v, qr_url='https://cdn.example.com/cert.pdf')
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    def test_generator_without_qr_url(self):
        prop, v = _make_passed_verification(self.org, self.dev)
        result = generate_trust_certificate(v, qr_url='')
        self.assertIsInstance(result, bytes)


# ── Celery Task ───────────────────────────────────────────────────────────────

class CertificateTaskTests(TestCase):

    def setUp(self):
        self.dev, self.org = make_developer()

    def _run_task(self, verification_id):
        mock_url = 'https://res.cloudinary.com/test/trust_certificates/cert.pdf'
        mock_upload = MagicMock(return_value={'secure_url': mock_url})
        with patch('cloudinary.uploader.upload', mock_upload), \
             patch('apps.verification.certificate.generate_trust_certificate',
                   return_value=b'%PDF-mock') as mock_gen:
            from apps.verification.tasks import generate_certificate_task
            generate_certificate_task(str(verification_id))
            return mock_url, mock_upload, mock_gen

    def test_task_sets_certificate_url(self):
        prop, v = _make_passed_verification(self.org, self.dev)
        mock_url, _, _ = self._run_task(v.pk)
        v.refresh_from_db()
        self.assertEqual(v.certificate_url, mock_url)

    def test_task_uploads_twice(self):
        """Two-pass: first pass without QR, second pass with QR."""
        prop, v = _make_passed_verification(self.org, self.dev)
        _, mock_upload, _ = self._run_task(v.pk)
        self.assertEqual(mock_upload.call_count, 2)

    def test_task_skipped_for_non_passed(self):
        prop, v = _make_passed_verification(self.org, self.dev)
        v.status = Verification.Status.FAILED
        v.save()
        mock_url = 'https://cdn.example.com/cert.pdf'
        with patch('cloudinary.uploader.upload', return_value={'secure_url': mock_url}):
            from apps.verification.tasks import generate_certificate_task
            generate_certificate_task(str(v.pk))
        v.refresh_from_db()
        self.assertEqual(v.certificate_url, '')

    def test_task_failure_does_not_raise(self):
        """Certificate failure is non-critical — task should not raise."""
        prop, v = _make_passed_verification(self.org, self.dev)
        with patch('cloudinary.uploader.upload', side_effect=RuntimeError('Cloudinary down')):
            from apps.verification.tasks import generate_certificate_task
            try:
                generate_certificate_task(str(v.pk))
            except Exception:
                self.fail('generate_certificate_task raised an exception on failure')


# ── Review View trigger ───────────────────────────────────────────────────────

class VerificationReviewTriggerTests(TestCase):

    def setUp(self):
        self.admin       = make_user(role='admin')
        self.dev, self.org = make_developer()
        prop             = make_property(org=self.org)
        _, self.verif    = make_scan(prop, self.dev)

    def _patch_and_review(self, new_status):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admin)
        with patch('apps.verification.tasks.generate_certificate_task.delay') as mock_cert, \
             patch('apps.verification.tasks.notify_verification_status_change.delay'):
            resp = client.patch(
                f'/api/v1/verification/queue/{self.verif.pk}/',
                {'status': new_status},
                format='json',
            )
            return resp, mock_cert

    def test_task_dispatched_on_passed(self):
        resp, mock_cert = self._patch_and_review('passed')
        self.assertEqual(resp.status_code, 200)
        mock_cert.assert_called_once_with(str(self.verif.pk))

    def test_task_not_dispatched_on_failed(self):
        resp, mock_cert = self._patch_and_review('failed')
        self.assertEqual(resp.status_code, 200)
        mock_cert.assert_not_called()


# ── TrustCertificateView ──────────────────────────────────────────────────────

class TrustCertificateViewTests(TestCase):

    def setUp(self):
        self.dev1, self.org1 = make_developer(org_name='Org A')
        self.dev2, self.org2 = make_developer(org_name='Org B')
        self.admin           = make_user(role='admin')
        self.agent           = make_user(role='agent')

        self.prop, self.verif = _make_passed_verification(self.org1, self.dev1)
        self.verif.certificate_url = 'https://cdn.example.com/cert.pdf'
        self.verif.save()

    def _get(self, user, property_id=None):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=user)
        pid = property_id or self.prop.pk
        return client.get(f'/api/v1/verification/{pid}/certificate/')

    def test_developer_gets_own_org_certificate(self):
        resp = self._get(self.dev1)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['certificate_url'], 'https://cdn.example.com/cert.pdf')

    def test_admin_gets_any_certificate(self):
        resp = self._get(self.admin)
        self.assertEqual(resp.status_code, 200)

    def test_agent_gets_403(self):
        resp = self._get(self.agent)
        self.assertEqual(resp.status_code, 403)

    def test_no_passed_verification_returns_404(self):
        prop2 = make_property(org=self.org1)
        resp  = self._get(self.dev1, property_id=prop2.pk)
        self.assertEqual(resp.status_code, 404)

    def test_generating_returns_202(self):
        self.verif.certificate_url = ''
        self.verif.save()
        resp = self._get(self.dev1)
        self.assertEqual(resp.status_code, 202)

    def test_cross_org_isolation(self):
        """Org B developer cannot get Org A's certificate."""
        resp = self._get(self.dev2)
        self.assertEqual(resp.status_code, 403)

    def test_response_fields_present(self):
        resp = self._get(self.dev1)
        self.assertEqual(resp.status_code, 200)
        for field in ('property_id', 'certificate_url', 'verified_at', 'signal_score'):
            self.assertIn(field, resp.data)
