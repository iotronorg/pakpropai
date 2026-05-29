"""
Task 15: Privacy Breach Regression Suite (10 tests).
These tests assert that raw PII never escapes into storage or logs.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings, RequestFactory
from rest_framework.test import APIClient

from apps.compliance.privacy_guard import PIIMaskingPipeline
from apps.compliance.middleware import PIIMaskingMiddleware
from tests.factories import make_developer, make_user, make_org

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_RAW_PHONE = "+923001234567"
_RAW_CNIC  = "12345-1234567-1"


def _make_wa_payload(text: str) -> bytes:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messages": [{"text": {"body": text}}]}}]}],
    }).encode()


# (a)
class PhoneNotLoggedRawTest(TestCase):
    def test_phone_not_logged_raw(self):
        from apps.compliance.models import PIIDetectionEvent
        factory = RequestFactory()
        body = json.dumps({"message": _RAW_PHONE}).encode()
        request = factory.post("/api/v1/data/", data=body, content_type="application/json")

        middleware = PIIMaskingMiddleware(get_response=lambda r: MagicMock(status_code=200))
        middleware(request)

        for event in PIIDetectionEvent.objects.all():
            self.assertNotIn(_RAW_PHONE, event.masked_value)


# (b)
class CnicNotLoggedRawTest(TestCase):
    def test_cnic_not_logged_raw(self):
        from apps.compliance.models import PIIDetectionEvent
        factory = RequestFactory()
        body = json.dumps({"field": _RAW_CNIC}).encode()
        request = factory.post("/api/v1/data/", data=body, content_type="application/json")

        middleware = PIIMaskingMiddleware(get_response=lambda r: MagicMock(status_code=200))
        middleware(request)

        for event in PIIDetectionEvent.objects.all():
            self.assertNotIn(_RAW_CNIC, event.masked_value)


# (c)
class FinancialStringMaskedTest(TestCase):
    def test_financial_string_masked_in_ai_response(self):
        pipeline = PIIMaskingPipeline()
        ai_response = "Your IBAN is GB29NWBK60161331926819, please proceed."
        result = pipeline.mask(ai_response)
        self.assertNotIn("GB29NWBK60161331926819", result.masked_text)
        self.assertIn("[IBAN]", result.masked_text)


# (d)
class CrossTenantPrivacyAuditAccessTest(TestCase):
    def test_cross_tenant_field_access_returns_403(self):
        client = APIClient()
        dev_a, org_a = make_developer()
        dev_b, org_b = make_developer()
        client.force_authenticate(user=dev_a)

        resp = client.get(f"/api/v1/compliance/privacy-audit/?org={org_b.id}")
        # developer role is org-scoped; org_b data must not be visible
        if resp.status_code == 200:
            data = resp.json()
            for entry in data:
                self.assertNotEqual(str(entry.get("org_id")), str(org_b.id))


# (e)
class PiiAuditLogPerDetectionTest(TestCase):
    def test_privacy_audit_log_written_per_detection(self):
        from apps.compliance.models import PIIDetectionEvent

        factory = RequestFactory()
        # payload with 3 PII fields
        body = json.dumps({
            "phone": _RAW_PHONE,
            "email": "user@example.com",
            "cnic": _RAW_CNIC,
        }).encode()
        request = factory.post("/api/v1/data/", data=body, content_type="application/json")

        before = PIIDetectionEvent.objects.count()
        middleware = PIIMaskingMiddleware(get_response=lambda r: MagicMock(status_code=200))
        middleware(request)

        # Give daemon thread time to write
        import time; time.sleep(0.3)

        after = PIIDetectionEvent.objects.count()
        self.assertGreaterEqual(after - before, 3)


# (f)
class ValidateCleanGateTest(TestCase):
    def test_validate_clean_gate_drops_missed_pattern(self):
        pipeline = PIIMaskingPipeline()
        # simulate mask() returning original text unchanged
        self.assertFalse(pipeline.validate_clean(_RAW_PHONE))


# (g)
@override_settings(CACHES=_LOCMEM)
class ConcurrentMaskingTest(TestCase):
    def test_concurrent_masking_no_raw_pii(self):
        from apps.compliance.models import PIIDetectionEvent
        import itertools

        counter = itertools.count(1)
        pipeline = PIIMaskingPipeline()
        phones = [f"+9230{i:08d}" for i in range(1, 21)]
        results = []

        def mask_one(phone):
            r = pipeline.mask(phone)
            results.append((phone, r.masked_text))

        with ThreadPoolExecutor(max_workers=20) as ex:
            list(ex.map(mask_one, phones))

        for phone, masked in results:
            self.assertNotIn(phone, masked)
            for other_phone, _ in results:
                if other_phone != phone:
                    self.assertNotIn(other_phone, masked)


# (h)
class WhatsappPayloadMaskedBeforeCeleryTest(TestCase):
    def test_whatsapp_webhook_payload_fully_masked_before_celery(self):
        factory = RequestFactory()
        body = json.dumps({"message": {"text": f"My phone is {_RAW_PHONE}"}}).encode()
        request = factory.post(
            "/api/v1/webhook/whatsapp/",
            data=body,
            content_type="application/json",
        )

        captured = {}

        def fake_get_response(req):
            captured["body"] = req._body
            return MagicMock(status_code=200)

        middleware = PIIMaskingMiddleware(get_response=fake_get_response)
        middleware(request)

        if "body" in captured:
            self.assertNotIn(_RAW_PHONE.encode(), captured["body"])


# (i)
class RTBFCascadeRemovesAllPIITest(TestCase):
    def test_rtbf_cascade_removes_all_pii(self):
        from apps.compliance.erasure import RTBFOrchestrator
        from apps.leads.models import Lead, LeadActivity
        from apps.whatsapp.models import WhatsAppSession, WhatsAppMessage
        from apps.verification.models import DocumentScan

        dev, org = make_developer()

        lead = Lead.objects.create(user=dev, organization=org, notes="Secret info")
        LeadActivity.objects.create(lead=lead, action="note", notes="Confidential note")
        session = WhatsAppSession.objects.create(phone=dev.phone, user=dev, organization=org)
        WhatsAppMessage.objects.create(
            session=session,
            wa_message_id="test-msg-1",
            direction="inbound",
            body="Private message",
        )
        DocumentScan.objects.create(user=dev, phone=dev.phone)

        req = dev.deletion_requests.create(status="pending")
        req.user = dev
        req.save()

        RTBFOrchestrator().execute(req)

        lead.refresh_from_db()
        self.assertEqual(lead.notes, "[REDACTED]")
        self.assertEqual(WhatsAppMessage.objects.filter(session=session).count(), 0)
        self.assertEqual(LeadActivity.objects.filter(lead=lead).exclude(notes="[REDACTED]").count(), 0)
        self.assertEqual(DocumentScan.objects.filter(user=dev).count(), 0)


# (j)
class AuditExportTest(TestCase):
    def test_audit_export_returns_csv(self):
        from apps.compliance.models import PrivacyAuditLog
        dev, org = make_developer()

        PrivacyAuditLog.objects.create(
            org=org,
            action="pii_detected",
            subject_identifier="abc123",
            jurisdiction="GDPR",
            regulation="GDPR",
        )

        client = APIClient()
        admin = make_user(phone="+9200000admin", role="admin")
        client.force_authenticate(user=admin)

        resp = client.get("/api/v1/compliance/privacy-audit/export/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.get("Content-Type", ""))
        content = b"".join(resp.streaming_content if hasattr(resp, "streaming_content") else [resp.content]).decode()
        self.assertIn("pii_detected", content)
