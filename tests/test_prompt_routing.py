"""
Prompt routing tests — verify that the correct market tokens appear
in the LLM system prompt based on the caller's WhatsApp phone number.

Run: python manage.py test tests.test_prompt_routing --settings=config.settings.test
"""
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase

from apps.ai.context import DynamicContextBuilder


def _build_context(phone: str) -> str:
    """Helper: build context with no-op org/lead blocks so only market block varies."""
    user = MagicMock(country='', role='client')
    with patch.object(DynamicContextBuilder, '_org_block', return_value=''), \
         patch.object(DynamicContextBuilder, '_lead_block', return_value=''):
        return DynamicContextBuilder.build(user=user, organization=None, phone=phone)


class PromptMarketInjectionTest(SimpleTestCase):

    def test_pakistan_number_injects_pkr_and_marla(self):
        ctx = _build_context('+923001234567')
        self.assertIn('PKR', ctx)
        self.assertIn('marla', ctx)
        self.assertNotIn('AED', ctx)

    def test_uae_number_injects_aed_and_sqft(self):
        ctx = _build_context('+971501234567')
        self.assertIn('AED', ctx)
        self.assertIn('sqft', ctx)
        self.assertNotIn('PKR', ctx)
        self.assertNotIn('marla', ctx)

    def test_uk_number_injects_gbp(self):
        ctx = _build_context('+447700900123')
        self.assertIn('GBP', ctx)
        self.assertNotIn('PKR', ctx)
        self.assertNotIn('AED', ctx)

    def test_us_number_injects_usd(self):
        ctx = _build_context('+12125551234')
        self.assertIn('USD', ctx)
        self.assertNotIn('PKR', ctx)

    def test_uae_prompt_contains_dld_tax_note(self):
        ctx = _build_context('+971501234567')
        self.assertIn('DLD', ctx)

    def test_pk_prompt_contains_fbr_tax_note(self):
        ctx = _build_context('+923001234567')
        self.assertIn('FBR', ctx)

    def test_uk_prompt_contains_sdlt_note(self):
        ctx = _build_context('+447700900123')
        self.assertIn('SDLT', ctx)

    def test_unknown_number_falls_back_to_pk(self):
        ctx = _build_context('+9991234567890')
        self.assertIn('PKR', ctx)
        self.assertIn('marla', ctx)

    def test_uae_market_rule_directive_references_aed(self):
        ctx = _build_context('+971501234567')
        self.assertIn('Always express prices in AED', ctx)

    def test_pk_market_rule_directive_references_pkr(self):
        ctx = _build_context('+923001234567')
        self.assertIn('Always express prices in PKR', ctx)

    def test_uae_size_unit_directive_references_sqft(self):
        ctx = _build_context('+971501234567')
        self.assertIn('sizes in sqft', ctx)

    def test_pk_size_unit_directive_references_marla(self):
        ctx = _build_context('+923001234567')
        self.assertIn('sizes in marla', ctx)

    def test_empty_phone_falls_back_to_pk(self):
        ctx = _build_context('')
        self.assertIn('PKR', ctx)


class OrgCountryOverridesPhoneTest(SimpleTestCase):
    """
    org.country takes highest priority — explicit org configuration always wins
    over the phone dial code, allowing multi-country orgs to serve the correct market.
    """

    def test_ae_org_overrides_pk_phone(self):
        user = MagicMock(country='')
        org  = MagicMock(country='AE', name='Gulf Estates', city='Dubai', plan='pro')
        org.get_org_type_display.return_value = 'Agency'

        with patch.object(DynamicContextBuilder, '_org_block', return_value=''), \
             patch.object(DynamicContextBuilder, '_lead_block', return_value=''):
            ctx = DynamicContextBuilder.build(
                user=user, organization=org, phone='+923001234567'
            )

        self.assertIn('AED', ctx)
        self.assertNotIn('marla', ctx)

    def test_pk_org_country_respected(self):
        user = MagicMock(country='')
        org  = MagicMock(country='PK', name='Lahore Dev', city='Lahore', plan='standard')
        org.get_org_type_display.return_value = 'Developer'

        with patch.object(DynamicContextBuilder, '_org_block', return_value=''), \
             patch.object(DynamicContextBuilder, '_lead_block', return_value=''):
            ctx = DynamicContextBuilder.build(
                user=user, organization=org, phone='+971501234567'
            )

        # Even though the phone is UAE, the org is PK — PK wins
        self.assertIn('PKR', ctx)
