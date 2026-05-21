"""Tests for AIServiceManager direct routes: loan_eligibility + property_audit."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.ai.service import AIServiceManager, IntentClassifier
from apps.ai.schemas import IntentResult, LoanEligibilityInput, AuditInput


def _loan_intent(income=100_000, loan_amount=5_000_000):
    return IntentResult(
        intent='loan_eligibility',
        confidence=0.87,
        language='en',
        loan_input=LoanEligibilityInput(
            monthly_income=income,
            loan_amount=loan_amount,
            tenure_years=20,
            scheme='conventional',
        ),
    )


def _audit_intent():
    return IntentResult(
        intent='property_audit',
        confidence=0.87,
        language='en',
        audit_input=AuditInput(
            city='Lahore',
            location='DHA Phase 5',
            property_type='residential',
            estimated_value_pkr=20_000_000,
            area_marla=5.0,
        ),
    )


class DirectLoanRouteTest(SimpleTestCase):
    """_try_direct_route returns loan reply when feature flag enabled."""

    def setUp(self):
        self.manager = AIServiceManager()

    @patch('apps.ai.service.AIServiceManager._direct_loan_eligibility', return_value='✅ Loan reply')
    @patch('apps.ai._tools_context.set_context')
    @patch('apps.config.services.SystemConfigService.get_features', return_value={'feature_loan_eligibility': True})
    def test_loan_direct_route_enabled(self, mock_flags, mock_ctx, mock_direct):
        intent = _loan_intent()
        reply = self.manager._try_direct_route(intent, '+923001234567')
        self.assertEqual(reply, '✅ Loan reply')
        mock_direct.assert_called_once()

    @patch('apps.ai._tools_context.set_context')
    @patch('apps.config.services.SystemConfigService.get_features', return_value={'feature_loan_eligibility': False})
    def test_loan_direct_route_disabled_returns_none(self, mock_flags, mock_ctx):
        intent = _loan_intent()
        reply = self.manager._try_direct_route(intent, '+923001234567')
        self.assertIsNone(reply)

    @patch('apps.ai._tools_context.set_context')
    @patch('apps.config.services.SystemConfigService.get_features', return_value={'feature_loan_eligibility': True})
    def test_loan_intent_without_loan_input_returns_none(self, mock_flags, mock_ctx):
        intent = IntentResult(intent='loan_eligibility', confidence=0.87, language='en')
        reply = self.manager._try_direct_route(intent, '+923001234567')
        self.assertIsNone(reply)


class DirectAuditRouteTest(SimpleTestCase):
    """_try_direct_route returns audit reply when feature flag enabled."""

    def setUp(self):
        self.manager = AIServiceManager()

    @patch('apps.ai.service.AIServiceManager._direct_property_audit', return_value='📊 Audit reply')
    @patch('apps.ai._tools_context.set_context')
    @patch('apps.config.services.SystemConfigService.get_features', return_value={'feature_property_audit': True})
    def test_audit_direct_route_enabled(self, mock_flags, mock_ctx, mock_direct):
        intent = _audit_intent()
        reply = self.manager._try_direct_route(intent, '+923001234567')
        self.assertEqual(reply, '📊 Audit reply')
        mock_direct.assert_called_once()

    @patch('apps.ai._tools_context.set_context')
    @patch('apps.config.services.SystemConfigService.get_features', return_value={'feature_property_audit': False})
    def test_audit_direct_route_disabled_returns_none(self, mock_flags, mock_ctx):
        intent = _audit_intent()
        reply = self.manager._try_direct_route(intent, '+923001234567')
        self.assertIsNone(reply)

    @patch('apps.ai._tools_context.set_context')
    @patch('apps.config.services.SystemConfigService.get_features', return_value={'feature_property_audit': True})
    def test_audit_intent_without_audit_input_returns_none(self, mock_flags, mock_ctx):
        intent = IntentResult(intent='property_audit', confidence=0.87, language='en')
        reply = self.manager._try_direct_route(intent, '+923001234567')
        self.assertIsNone(reply)
