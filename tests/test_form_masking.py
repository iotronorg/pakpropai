"""KI-094/KI-097 — form bodies are masked; provider callbacks keep their body and must prove themselves."""
import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import get_resolver
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.test import APIClient

from apps.compliance.parsers import MaskedFormParser, MaskedMultiPartParser
from apps.config.models import SystemConfig
from apps.leads.models import ConversationMessage
from apps.organizations.models import Organization
from apps.verification.models import DocumentScan
from apps.verification.providers.base import VerificationResult
from tests.factories import make_developer, make_org
from tests.test_enquiries import THR, Base
from tests.test_runtime import LOCMEM

PHONE, EMAIL = '+923001234567', 'a@mail.example'
FORM = 'application/x-www-form-urlencoded'


def views(patterns=None):
    for p in get_resolver().url_patterns if patterns is None else patterns:
        if hasattr(p, 'url_patterns'):
            yield from views(p.url_patterns)
        elif cls := getattr(p.callback, 'cls', None) or getattr(p.callback, 'view_class', None):
            yield cls, getattr(p.callback, 'initkwargs', {})


class FormTest(Base):
    def say_form(self, user, e, body, **kw):
        return self.api(user).post(f'{THR}{e.pk}/messages/', body, **kw)

    def test_form_and_multipart_equal_json(self):
        e = self.thread()
        text = f'Call {PHONE} or {EMAIL}'
        expected = self.say(self.buyer, e, text).data['body']
        self.assertNotIn(PHONE, expected)
        from urllib.parse import urlencode
        for r in (self.say_form(self.buyer, e, urlencode({'body': text}), content_type=FORM),
                  self.say_form(self.buyer, e, {'body': text}, format='multipart')):
            self.assertEqual((r.status_code, r.data['body']), (201, expected))
        self.assertFalse(ConversationMessage.objects.filter(body__contains=EMAIL).exists())

    def test_json_content_type_in_any_case(self):
        e = self.thread()
        r = self.say_form(self.buyer, e, json.dumps({'body': f'Mail {EMAIL}'}), content_type='Application/JSON')
        self.assertNotIn(EMAIL, r.data['body'])

    def test_allow_list_applies_to_forms(self):
        dev, org = make_developer()
        c = self.api(dev)
        r = c.patch('/api/v1/organizations/me/', {'phone': PHONE, 'description': f'Call {PHONE}'}, format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        org.refresh_from_db()
        self.assertEqual(org.phone, PHONE)
        self.assertNotIn(PHONE, str(r.data.get('description', '')))

    def test_admin_creates_an_organization_with_its_contact(self):
        self.buyer.role = 'admin'
        self.buyer.save()
        for fmt in ('multipart', 'json'):
            r = self.api(self.buyer).post('/api/v1/organizations/', {'name': f'New {fmt}', 'phone': PHONE}, format=fmt)
            self.assertEqual(r.status_code, 201, r.data)
            self.assertEqual(Organization.objects.get(name=f'New {fmt}').phone, PHONE)


class EveryViewTest(TestCase):
    def test_no_view_parses_forms_unmasked(self):
        for cls, kw in views():
            for parser in kw.get('parser_classes') or getattr(cls, 'parser_classes', ()):
                if issubclass(parser, (FormParser, MultiPartParser)):
                    self.assertTrue(issubclass(parser, (MaskedFormParser, MaskedMultiPartParser)), cls)


@override_settings(CACHES=LOCMEM)
class CallbackTest(TestCase):
    def setUp(self):
        cache.clear()
        self.c = APIClient()

    def stripe(self, event, secret='whsec_test', header=True):
        body, t = json.dumps(event), int(time.time())
        sig = hmac.new(secret.encode(), f'{t}.{body}'.encode(), hashlib.sha256).hexdigest()
        kw = {'HTTP_STRIPE_SIGNATURE': f't={t},v1={sig}'} if header else {}
        return self.c.post('/api/v1/billing/webhook/stripe/', body, content_type='application/json', **kw)

    def event(self, **obj):
        return {'id': 'evt_1', 'object': 'event', 'type': 'checkout.session.completed', 'data': {'object': {
            'id': 'cs_1', 'object': 'checkout.session', 'customer_details': {'email': EMAIL, 'phone': PHONE}, **obj}}}

    def test_stripe_event_with_a_customer_email_is_verified_and_handled(self):
        SystemConfig.objects.create(key='stripe_webhook_secret', value='whsec_test')
        org = make_org(name='Paying')
        r = self.stripe(self.event(metadata={'org_id': str(org.pk), 'plan': 'professional'}, subscription='sub_1',
                                   customer='cus_1'))
        self.assertEqual(r.status_code, 200, r.data)
        org.refresh_from_db()
        self.assertEqual(org.plan, 'professional')

    def test_a_failed_event_is_handled_when_stripe_retries(self):
        SystemConfig.objects.create(key='stripe_webhook_secret', value='whsec_test')
        target = 'apps.billing.stripe_service.StripeService.handle_checkout_completed'
        with patch(target, side_effect=OSError):
            self.assertEqual(self.stripe(self.event()).status_code, 500)
        with patch(target) as handle:
            self.assertEqual(self.stripe(self.event()).status_code, 200)
            self.assertEqual(self.stripe(self.event()).status_code, 200)     # a duplicate
        handle.assert_called_once()
        self.assertEqual(handle.call_args.args[0]['customer_details'], {'email': EMAIL, 'phone': PHONE})

    def test_other_stripe_callbacks_read_real_events(self):
        for path, name, kind in (('payments/webhook/stripe/deal-lock/', 'STRIPE_DEAL_LOCK_WEBHOOK_SECRET',
                                  'checkout.session.completed'),
                                 ('verification/webhook/stripe-identity/', 'STRIPE_IDENTITY_WEBHOOK_SECRET',
                                  'identity.verification_session.verified')):
            event, t = {**self.event(), 'type': kind}, int(time.time())
            body = json.dumps(event)
            sig = hmac.new(b'whsec_x', f'{t}.{body}'.encode(), hashlib.sha256).hexdigest()
            with override_settings(**{name: 'whsec_x'}):
                r = self.c.post(f'/api/v1/{path}', body, content_type='application/json',
                                HTTP_STRIPE_SIGNATURE=f't={t},v1={sig}')
            self.assertEqual((r.status_code, r.data), (200, {'received': True, 'activated': False}), path)

    def test_stripe_refuses_unsigned_and_unconfigured(self):
        org = make_org(name='Free')
        event = self.event(metadata={'org_id': str(org.pk), 'plan': 'enterprise'})
        self.assertEqual(self.stripe(event).status_code, 400)                  # no secret configured
        self.assertEqual(self.stripe(event, secret='').status_code, 400)       # signed with an empty key
        SystemConfig.objects.create(key='stripe_webhook_secret', value='whsec_test')
        cache.clear()
        self.assertEqual(self.stripe(event, secret='other').status_code, 400)
        self.assertEqual(self.stripe(event, header=False).status_code, 400)
        org.refresh_from_db()
        self.assertNotEqual(org.plan, 'enterprise')

    def test_signed_bodies_reach_every_callback_unchanged(self):
        body = json.dumps({'buyer': {'email': EMAIL, 'phone': PHONE}}, separators=(',', ':')).encode()
        for path, target in (('payments/webhook/safepay/', 'apps.payments.views.PaymentService.handle_webhook'),
                             ('payments/webhook/bsecure/', 'apps.payments.views.PaymentService.handle_webhook')):
            with patch(target, return_value=False) as handle:
                self.c.post(f'/api/v1/{path}', body, content_type='application/json')
            self.assertEqual(handle.call_args.args[2], body)
        for path in ('billing/webhook/stripe/', 'billing/webhook/safepay/', 'billing/webhook/bsecure/',
                     'payments/webhook/stripe/deal-lock/', 'payments/webhook/safepay/deal-lock/',
                     'verification/webhook/onfido/', 'verification/webhook/stripe-identity/', 'voice/webhook/inbound/'):
            from apps.compliance.middleware import is_exempt
            self.assertTrue(is_exempt(f'/api/v1/{path}'), path)

    def test_stored_audit_copy_is_masked(self):
        from apps.payments.services import masked_payload
        out = masked_payload({'type': 'x', 'id': 'evt_1', 'data': {'object': {'customer_details': {'email': EMAIL, 'phone': PHONE}}}})
        self.assertEqual((out['id'], out['data']['object']['customer_details']),
                         ('evt_1', {'email': '[EMAIL]', 'phone': '[PHONE]'}))

    def test_every_stored_payload_goes_through_the_mask(self):
        from pathlib import Path
        import re
        for name in ('views', 'services'):
            text = Path(f'apps/payments/{name}.py').read_text()
            for m in re.findall(r'webhook_payload=(\w+)', text):
                self.assertEqual(m, 'masked_payload')

    def jumio(self, decision='PASSED'):
        return self.c.post('/api/v1/verification/webhook/jumio/', {
            'account': {'id': 'acc'}, 'workflowExecution': {'id': 'wf', 'decision': {'type': decision}}}, format='json')

    def test_jumio_result_comes_from_jumio_not_from_the_caller(self):
        scan = DocumentScan.objects.create(registration_number='acc::wf', status=DocumentScan.Status.UNREADABLE,
                                           document_type=DocumentScan.DocType.PASSPORT)
        poll = 'apps.verification.providers.jumio_provider.JumioVerificationProvider.poll_result'
        self.assertEqual(self.jumio().status_code, 503)                        # no credentials: nothing to trust
        for outcome in (None, VerificationResult(status='pending', confidence=0.5, extracted_fields={}, red_flags=[])):
            with patch(poll, return_value=outcome):
                self.assertEqual(self.jumio().status_code, 503)
        with patch(poll, side_effect=OSError):
            self.assertEqual(self.jumio().status_code, 503)
        scan.refresh_from_db()
        self.assertEqual(scan.status, DocumentScan.Status.UNREADABLE)
        with patch(poll, return_value=VerificationResult(status='declined', confidence=0, extracted_fields={}, red_flags=[])):
            self.assertEqual(self.jumio('PASSED').status_code, 200)
        scan.refresh_from_db()
        self.assertEqual(scan.status, DocumentScan.Status.SUSPICIOUS)

    def test_jumio_unknown_session_and_bad_payload(self):
        with patch('apps.verification.providers.jumio_provider.JumioVerificationProvider.poll_result') as poll:
            self.assertEqual(self.jumio().data['activated'], False)
            for bad in ({'account': 'x'}, [1], {'workflowExecution': [1]}):
                self.assertEqual(self.c.post('/api/v1/verification/webhook/jumio/', bad, format='json').status_code, 400)
        poll.assert_not_called()
