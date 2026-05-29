"""
Tests for AMLScreeningGateway + ComplianceService (16 tests).

Coverage:
  a  exact name match → blocked
  b  fuzzy match above threshold → flagged
  c  fuzzy match below threshold → clear
  d  id_number hash match → blocked
  e  name normalisation (diacritics / case / whitespace)
  f  platform-level record (org=None) matches any org
  g  org-level record not matched for different org
  h  screen_deal_lock cancels deal on block
  i  admin notification created on block
  j  AuditLog row created on block (action=reject)
  k  WhatsApp sessions set to BLOCKED on block
  l  fail-open on exception (returns clear + no exception raised)
  m  clear entity passes through (no DB row for clear)
  n  screening result persisted to DB on match
  o  calculate_deal_tax returns PK result for PK org
  p  calculate_deal_tax returns supported=False for AE org
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase

from apps.compliance.models import ComplianceSanctionRecord, SanctionScreeningResult
from apps.compliance.aml_gateway import AMLScreeningGateway, ScreeningResult
from tests.factories import make_developer, make_user, make_property, make_deal


class ExactNameMatchTest(TestCase):
    """a — exact name match → blocked."""

    def test_a_exact_name_returns_blocked(self):
        _, org = make_developer()
        ComplianceSanctionRecord.objects.create(
            name='John Doe',
            list_source='LOCAL',
            risk_level='high',
        )
        result = AMLScreeningGateway.screen_entity('John Doe', '', 'cnic', org)
        self.assertEqual(result.status, 'blocked')
        self.assertEqual(result.risk_score, 100)


class FuzzyAboveThresholdTest(TestCase):
    """b — fuzzy match ≥ 0.85 → flagged."""

    def test_b_fuzzy_above_threshold_returns_flagged(self):
        _, org = make_developer()
        ComplianceSanctionRecord.objects.create(
            name='Muhammad Ali Khan',
            list_source='LOCAL',
            risk_level='high',
        )
        # Close variant — should hit fuzzy threshold
        result = AMLScreeningGateway.screen_entity('Muhammad Ali Khn', '', 'cnic', org)
        self.assertEqual(result.status, 'flagged')


class FuzzyBelowThresholdTest(TestCase):
    """c — fuzzy match < 0.85 → clear."""

    def test_c_fuzzy_below_threshold_returns_clear(self):
        _, org = make_developer()
        ComplianceSanctionRecord.objects.create(
            name='Totally Unrelated Name',
            list_source='LOCAL',
        )
        result = AMLScreeningGateway.screen_entity('xyz qrs', '', 'cnic', org)
        self.assertEqual(result.status, 'clear')


class IDHashMatchTest(TestCase):
    """d — id_number hash match → blocked."""

    def test_d_id_hash_match_returns_blocked(self):
        _, org = make_developer()
        id_number = '1234-5678901-2'
        hash_val = AMLScreeningGateway._hash_id(id_number)
        ComplianceSanctionRecord.objects.create(
            name='Alias Name',
            id_number_prefix=id_number[:4],
            id_number_hash=hash_val,
            id_type='cnic',
            list_source='OFAC',
        )
        result = AMLScreeningGateway.screen_entity('Different Name', id_number, 'cnic', org)
        self.assertEqual(result.status, 'blocked')
        self.assertEqual(result.matched_records[0].id_number_hash, hash_val)


class NameNormalisationTest(TestCase):
    """e — normalisation: diacritics, case, extra whitespace."""

    def test_e_normalisation_matches_diacritics(self):
        _, org = make_developer()
        ComplianceSanctionRecord.objects.create(name='Café Owner', list_source='LOCAL')
        # Accent stripped during normalisation — should match
        result = AMLScreeningGateway.screen_entity('Cafe Owner', '', 'cnic', org)
        # exact after normalisation
        self.assertEqual(result.status, 'blocked')

    def test_e_normalisation_case_insensitive(self):
        _, org = make_developer()
        ComplianceSanctionRecord.objects.create(name='UPPER CASE', list_source='LOCAL')
        result = AMLScreeningGateway.screen_entity('upper case', '', 'cnic', org)
        self.assertEqual(result.status, 'blocked')


class PlatformLevelRecordTest(TestCase):
    """f — platform-level record (org=None) matches any org."""

    def test_f_platform_record_matches_any_org(self):
        _, org_a = make_developer(org_name='Org A')
        _, org_b = make_developer(org_name='Org B')
        # Platform-level: org=None
        ComplianceSanctionRecord.objects.create(
            name='Global Sanctions Target',
            list_source='UN',
            org=None,
        )
        result_a = AMLScreeningGateway.screen_entity('Global Sanctions Target', '', 'cnic', org_a)
        result_b = AMLScreeningGateway.screen_entity('Global Sanctions Target', '', 'cnic', org_b)
        self.assertEqual(result_a.status, 'blocked')
        self.assertEqual(result_b.status, 'blocked')


class OrgLevelIsolationTest(TestCase):
    """g — org-level record not matched for different org."""

    def test_g_org_record_not_visible_to_other_org(self):
        _, org_a = make_developer(org_name='Org A')
        _, org_b = make_developer(org_name='Org B')
        ComplianceSanctionRecord.objects.create(
            name='Org A Only Target',
            list_source='LOCAL',
            org=org_a,
        )
        result = AMLScreeningGateway.screen_entity('Org A Only Target', '', 'cnic', org_b)
        self.assertEqual(result.status, 'clear')


class DealCancelledOnBlockTest(TestCase):
    """h — screen_deal_lock cancels deal on block."""

    def test_h_deal_cancelled_on_block(self):
        from apps.compliance.compliance_service import ComplianceService
        from apps.escrow.models import EscrowDeal

        buyer, _ = make_user(), None
        buyer = make_user(role='client')
        _, org = make_developer()
        prop = make_property(org=org)
        deal = make_deal(buyer, prop)

        ComplianceSanctionRecord.objects.create(
            name=buyer.name or 'Test Buyer',
            list_source='LOCAL',
        )
        # Patch name so it matches
        buyer.name = 'Test Buyer'
        buyer.save(update_fields=['name'])

        ComplianceService.screen_deal_lock(deal, buyer)
        deal.refresh_from_db()
        self.assertEqual(deal.status, EscrowDeal.Status.CANCELLED)


class AdminNotificationOnBlockTest(TestCase):
    """i — admin notification created on block."""

    def test_i_admin_notification_on_block(self):
        from apps.compliance.compliance_service import ComplianceService
        from apps.notifications.models import Notification

        admin_user, org = make_developer()
        buyer = make_user(role='client', name='Blocked Buyer')
        prop  = make_property(org=org)
        deal  = make_deal(buyer, prop)

        ComplianceSanctionRecord.objects.create(name='Blocked Buyer', list_source='LOCAL')
        ComplianceService.screen_deal_lock(deal, buyer)

        notif = Notification.objects.filter(user=admin_user).first()
        self.assertIsNotNone(notif)
        self.assertIn('AML', notif.title)


class AuditLogOnBlockTest(TestCase):
    """j — AuditLog row created on block (action=reject)."""

    def test_j_audit_log_created_on_block(self):
        from apps.compliance.compliance_service import ComplianceService
        from apps.core.models import AuditLog

        _, org   = make_developer()
        buyer    = make_user(role='client', name='Sanctioned Entity')
        prop     = make_property(org=org)
        deal     = make_deal(buyer, prop)

        ComplianceSanctionRecord.objects.create(name='Sanctioned Entity', list_source='OFAC')
        ComplianceService.screen_deal_lock(deal, buyer)

        log = AuditLog.objects.filter(
            action=AuditLog.Action.REJECT,
            target_model='EscrowDeal',
            target_id=str(deal.id),
        ).first()
        self.assertIsNotNone(log)
        self.assertIn('aml_block', log.detail)


class WhatsAppBlockedOnAMLTest(TestCase):
    """k — WhatsApp sessions set to BLOCKED on block."""

    def test_k_wa_sessions_blocked_on_aml(self):
        from apps.compliance.compliance_service import ComplianceService
        from apps.whatsapp.models import WhatsAppSession

        _, org = make_developer()
        buyer  = make_user(role='client', name='WA Blocked Person')
        prop   = make_property(org=org)
        deal   = make_deal(buyer, prop)

        # Create active WA session
        WhatsAppSession.objects.create(
            phone=buyer.phone,
            conversation_mode=WhatsAppSession.ConversationMode.AI_MANAGED,
        )

        ComplianceSanctionRecord.objects.create(name='WA Blocked Person', list_source='LOCAL')
        ComplianceService.screen_deal_lock(deal, buyer)

        session = WhatsAppSession.objects.get(phone=buyer.phone)
        self.assertEqual(session.conversation_mode, WhatsAppSession.ConversationMode.BLOCKED)


class FailOpenOnExceptionTest(TestCase):
    """l — fail-open: exception during screening → clear, no exception raised."""

    def test_l_fail_open_returns_clear(self):
        _, org = make_developer()
        with patch('apps.compliance.models.ComplianceSanctionRecord.objects') as mock_qs:
            mock_qs.filter.side_effect = RuntimeError('DB error')
            result = AMLScreeningGateway.screen_entity('Anyone', '1234', 'cnic', org)
        self.assertEqual(result.status, 'clear')
        self.assertEqual(result.risk_score, 0)


class ClearEntityNoDBRowTest(TestCase):
    """m — clear entity leaves no SanctionScreeningResult row."""

    def test_m_clear_entity_no_db_row(self):
        _, org = make_developer()
        result = AMLScreeningGateway.screen_entity('Clean Person', '', 'cnic', org)
        self.assertEqual(result.status, 'clear')
        self.assertEqual(SanctionScreeningResult.objects.count(), 0)


class ScreeningResultPersistedTest(TestCase):
    """n — screening result persisted to DB on match."""

    def test_n_screening_result_in_db_on_match(self):
        _, org = make_developer()
        ComplianceSanctionRecord.objects.create(name='DB Persist Target', list_source='EU')
        AMLScreeningGateway.screen_entity('DB Persist Target', '', 'cnic', org)
        self.assertEqual(SanctionScreeningResult.objects.filter(org=org).count(), 1)
        sr = SanctionScreeningResult.objects.get(org=org)
        self.assertEqual(sr.status, 'blocked')
        self.assertEqual(sr.list_source, 'EU')


class CalculateDealTaxPKTest(TestCase):
    """o — calculate_deal_tax returns PK result for PK org."""

    def test_o_pk_org_returns_pk_tax_result(self):
        from apps.compliance.compliance_service import ComplianceService

        _, org = make_developer()
        org.country = 'PK'
        org.save(update_fields=['country'])

        prop  = make_property(org=org, price=5_000_000)
        buyer = make_user(role='client')
        deal  = make_deal(buyer, prop, token_amount=100_000)

        result = ComplianceService.calculate_deal_tax(deal, org)
        self.assertTrue(result.supported)
        self.assertEqual(result.country, 'PK')
        self.assertGreater(result.wht_amount, 0)


class CalculateDealTaxAETest(TestCase):
    """p — calculate_deal_tax returns supported=False for AE org."""

    def test_p_ae_org_returns_unsupported(self):
        from apps.compliance.compliance_service import ComplianceService

        _, org = make_developer()
        org.country = 'AE'
        org.save(update_fields=['country'])

        prop  = make_property(org=org)
        buyer = make_user(role='client')
        deal  = make_deal(buyer, prop, token_amount=50_000)

        result = ComplianceService.calculate_deal_tax(deal, org)
        self.assertFalse(result.supported)
