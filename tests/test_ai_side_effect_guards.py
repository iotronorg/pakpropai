"""
T-0007 — side-effect AI tools require server-side confirmation; deal lock frozen.
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


class AffirmativeParsingTest(TestCase):

    def test_affirmatives(self):
        for msg in ('yes', 'YES!', 'haan', 'ji haan', 'ok', 'confirm', 'theek hai', 'ہاں'):
            self.assertTrue(confirmation.is_affirmative(msg), msg)

    def test_non_affirmatives(self):
        for msg in ('no', 'nahi', "yes but don't publish", 'not yet', '', 'maybe',
                    'list my 10 marla house in DHA for 2.5 crore please'):
            self.assertFalse(confirmation.is_affirmative(msg), msg)


class ListPropertyConfirmationTest(TestCase):

    def setUp(self):
        self.user = make_client(phone='+923001234598')

    def test_first_call_only_proposes(self):
        _turn(self.user, 'list my house: 10 marla DHA 6, 2.5 crore')
        result = list_property(**LISTING)
        self.assertTrue(result['confirmation_required'])
        self.assertIn('PKR 25,000,000', result['message'])
        self.assertFalse(Property.objects.filter(owner=self.user).exists())

    def test_same_turn_repeat_cannot_self_confirm(self):
        _turn(self.user, 'yes')  # even an affirmative message in the proposing turn
        list_property(**LISTING)
        result = list_property(**LISTING)
        self.assertTrue(result['confirmation_required'])
        self.assertFalse(Property.objects.filter(owner=self.user).exists())

    def test_confirmed_in_next_turn_creates_listing(self):
        _turn(self.user, 'list my house')
        list_property(**LISTING)
        _turn(self.user, 'yes')
        result = list_property(**LISTING)
        self.assertTrue(result['success'], result)
        self.assertEqual(Property.objects.filter(owner=self.user).count(), 1)

    def test_negative_reply_does_not_create(self):
        _turn(self.user, 'list my house')
        list_property(**LISTING)
        _turn(self.user, 'no')
        result = list_property(**LISTING)
        self.assertTrue(result['confirmation_required'])
        self.assertFalse(Property.objects.filter(owner=self.user).exists())

    def test_changed_details_need_fresh_confirmation(self):
        _turn(self.user, 'list my house')
        list_property(**LISTING)
        _turn(self.user, 'yes')
        result = list_property(**{**LISTING, 'price': 30_000_000})
        self.assertTrue(result['confirmation_required'])
        self.assertFalse(Property.objects.filter(owner=self.user).exists())

    def test_confirmation_is_single_use(self):
        _turn(self.user, 'list my house')
        list_property(**LISTING)
        _turn(self.user, 'yes')
        list_property(**LISTING)
        _turn(self.user, 'yes')
        result = list_property(**LISTING)
        self.assertTrue(result['confirmation_required'])
        self.assertEqual(Property.objects.filter(owner=self.user).count(), 1)


class ConnectToAgentConfirmationTest(TestCase):

    def setUp(self):
        self.user = make_client(phone='+923001234597')

    def test_llm_call_requires_confirmation(self):
        _turn(self.user, 'I might want help buying')
        result = connect_to_agent(city='Lahore', intent='buy')
        self.assertTrue(result['confirmation_required'])
        self.assertIn('number will be shared', result['message'])

    def test_explicit_user_request_path_is_not_blocked(self):
        _turn(self.user, 'connect me to an agent in Lahore')
        confirmation.mark_explicit_request()
        result = connect_to_agent(city='Lahore', intent='buy')
        self.assertNotIn('confirmation_required', result)

    def test_explicit_flag_reset_on_next_turn(self):
        _turn(self.user, 'connect me to an agent')
        confirmation.mark_explicit_request()
        _turn(self.user, 'what about Karachi')
        result = connect_to_agent(city='Karachi', intent='buy')
        self.assertTrue(result['confirmation_required'])


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
        self.assertIn('search_properties', names)

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
        _turn(self.user, 'audit it')
        self._generate()
        _turn(self.user, 'yes')
        result = self._generate()
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
