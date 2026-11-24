"""
T-0007 / T-2012 — side-effect AI tools require server-side confirmation; deal lock frozen.
"""
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.ai import confirmation
from apps.ai._tools_context import set_context
from apps.ai.tools import connect_to_agent, initiate_deal_lock, list_property
from apps.properties.models import Property
from tests.factories import enable_feature, make_client, make_org, make_property

PHONE = '+923001234599'
LISTING = dict(city='Lahore', location='DHA Phase 6', area_marla=10,
               price=25_000_000, property_type='residential')


def _turn(user, message):
    """Simulate a new inbound WhatsApp message (new turn)."""
    set_context(user, PHONE, org=None, message=message)


def _reply(user, message):
    """The user's next message, as AIServiceManager.process handles it."""
    return confirmation.resolve_reply(PHONE, message, user, None)


class AffirmativeParsingTest(TestCase):

    def test_affirmatives(self):
        for msg in ('yes', 'YES!', 'haan', 'ji haan', 'ok', 'confirm', 'theek hai', 'ہاں', 'Yes please', 'yes 👍'):
            self.assertTrue(confirmation.is_affirmative(msg), msg)

    def test_non_affirmatives(self):
        for msg in ('no', 'nahi', "yes but don't publish", 'not yet', '', 'maybe', 'please',
                    'yes how much is the fee?', 'ok show me flats in Karachi', '👍',
                    'list my 10 marla house in DHA for 2.5 crore please'):
            self.assertFalse(confirmation.is_affirmative(msg), msg)

    def test_negatives(self):
        for msg in ('no', 'No thanks', 'cancel', 'nahi', 'نہیں', 'don’t'):
            self.assertTrue(confirmation.is_negative(msg), msg)
        for msg in ('no make it 3 crore', 'thanks', 'yes', ''):
            self.assertFalse(confirmation.is_negative(msg), msg)


class ListPropertyConfirmationTest(TestCase):

    def setUp(self):
        self.user = make_client(phone='+923001234598')

    def _count(self):
        return Property.objects.filter(owner=self.user).count()

    def test_calls_only_propose_even_when_repeated_after_yes(self):
        for msg in ('list my house: 10 marla DHA 6, 2.5 crore', 'yes', 'yes'):
            _turn(self.user, msg)
            result = list_property(**LISTING)          # the model calling the tool
            self.assertTrue(result['confirmation_required'])
        self.assertIn('PKR 25,000,000', result['message'])
        self.assertEqual(self._count(), 0)

    def test_yes_executes_the_stored_call_once(self):
        _turn(self.user, 'list my house')
        list_property(**LISTING)
        self.assertIn('Draft saved', _reply(self.user, 'yes'))
        self.assertIsNone(_reply(self.user, 'yes'))
        self.assertEqual(self._count(), 1)

    def test_negative_reply_declines(self):
        _turn(self.user, 'list my house')
        list_property(**LISTING)
        self.assertIn('cancelled', _reply(self.user, 'no'))
        self.assertIsNone(_reply(self.user, 'yes'))
        self.assertEqual(self._count(), 0)

    def test_changed_details_replace_the_proposal(self):
        _turn(self.user, 'list my house')
        list_property(**LISTING)
        _turn(self.user, 'make it 3 crore')
        list_property(**{**LISTING, 'price': 30_000_000})
        _reply(self.user, 'yes')
        self.assertEqual(Property.objects.get(owner=self.user).price, 30_000_000)


class ConnectToAgentConfirmationTest(TestCase):

    def test_call_requires_confirmation(self):
        user = make_client(phone='+923001234597')
        _turn(user, 'connect me to an agent in Lahore')
        result = connect_to_agent(city='Lahore', intent='buy')
        self.assertTrue(result['confirmation_required'])
        self.assertIn('number will be shared', result['message'])


class DealLockFrozenTest(TestCase):

    def setUp(self):
        self.user = make_client(phone='+923001234596')
        self.prop = make_property(org=make_org(name='P', operational_mode='production'))

    def test_tool_refuses_by_default(self):
        set_context(self.user, PHONE, org=None, message='lock it')
        result = initiate_deal_lock(property_id=str(self.prop.id), token_amount=25_000)
        self.assertFalse(result['success'])
        self.assertIn('not available', result['error'])

    def test_tool_not_offered_to_llm_by_default(self):
        from apps.ai import tools as tool_module
        from apps.ai.agent import RealTronAgent
        names = {fn.__name__ for fn in RealTronAgent()._get_tools(tool_module)}
        self.assertNotIn('initiate_deal_lock', names)
        self.assertIn('search_listings', names)

    def test_initiate_endpoint_disabled_by_default(self):
        api = APIClient()
        api.force_authenticate(self.user)
        resp = api.post(reverse('deal-initiate'), {
            'property_id': str(self.prop.pk), 'token_amount': 25_000, 'payment_gateway': 'jazzcash',
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['code'], 'feature_disabled')

    def test_deal_list_still_available(self):
        api = APIClient()
        api.force_authenticate(self.user)
        self.assertEqual(api.get(reverse('deal-mine')).status_code, 200)

    def test_operator_can_reenable(self):
        enable_feature('feature_deal_lock')
        set_context(self.user, PHONE, org=None, message='lock it')
        result = initiate_deal_lock(property_id=str(self.prop.id), token_amount=25_000)
        self.assertNotIn('not available', str(result.get('error', '')))


class AuditToolGuardAndDisclaimerTest(TestCase):
    """T-0017 confirmation + T-0013 'not a valuation' disclaimer and provenance."""

    AUDIT = dict(city='Lahore', location='DHA Phase 5', estimated_value_pkr=30_000_000,
                 property_type='residential', area_marla=10)

    def setUp(self):
        self.user = make_client(phone='+923001234595')

    def _generate(self):
        from unittest.mock import patch
        from apps.ai.tools import generate_property_audit
        with patch('apps.audit.pdf.generate_audit_pdf'):
            return generate_property_audit(**self.AUDIT)

    def test_llm_call_requires_confirmation(self):
        from apps.audit.models import PropertyAudit
        _turn(self.user, 'can you check this house')
        result = self._generate()
        self.assertTrue(result['confirmation_required'])
        self.assertIn('not a valuation', result['whatsapp_summary'])
        self.assertFalse(PropertyAudit.objects.exists())

    def test_confirmed_audit_has_disclaimer_and_provenance(self):
        from unittest.mock import patch
        from apps.ai.models import PendingAction
        _turn(self.user, 'audit it')
        proposal = self._generate()
        with patch('apps.audit.pdf.generate_audit_pdf'):
            result = confirmation.confirm(PendingAction.objects.get(pk=proposal['pending_action_id']), 'message')
        self.assertTrue(result['success'], result)
        summary = result['whatsapp_summary']
        self.assertIn('Not a valuation', summary)
        self.assertIn('Indicative Range (not a valuation)', summary)
        self.assertIn('Figures are estimates from the', summary)

    def test_provenance_recorded(self):
        from apps.audit.services import AuditEngine
        data = AuditEngine.run(city='Lahore', location='DHA Phase 5', property_type='residential',
                               estimated_value_pkr=30_000_000, area_marla=10)
        prov = data['provenance']
        self.assertFalse(prov['is_valuation'])
        self.assertEqual(prov['engine'], 'deterministic_benchmark_v1')
        self.assertIn(prov['benchmark_source'], {'admin_configured', 'built_in_default'})
        unknown = AuditEngine.run(city='Atlantis', location='Nowhere', property_type='residential',
                                  estimated_value_pkr=30_000_000, area_marla=10)
        self.assertEqual(unknown['provenance']['benchmark_match'], 'global')

    def test_pdf_contains_disclaimer(self):
        import os
        import tempfile
        from apps.audit.pdf import generate_audit_pdf
        from apps.audit.services import AuditEngine
        data = AuditEngine.run(city='Lahore', location='DHA Phase 5', property_type='residential',
                               estimated_value_pkr=30_000_000, area_marla=10)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'a.pdf')
            generate_audit_pdf(data, path)
            self.assertGreater(os.path.getsize(path), 1000)
        # Text extraction is not available in the image; assert the builders include it.
        from apps.audit import pdf as pdf_mod
        from reportlab.platypus import Paragraph
        page1 = pdf_mod._page1_executive_summary(data, pdf_mod._styles())
        texts = ' '.join(e.text for e in page1 if isinstance(e, Paragraph))
        self.assertIn('Not a valuation', texts)
        self.assertNotIn('generated by AI', ' '.join(
            e.text for e in pdf_mod._page7_recommendations(data, pdf_mod._styles()) if isinstance(e, Paragraph)
        ))
