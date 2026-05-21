"""Tests for IntentClassifier: _extract_loan_input, _extract_audit_input, regression cases."""

from django.test import SimpleTestCase

from apps.ai.service import IntentClassifier


class LoanInputExtractionTest(SimpleTestCase):
    """IntentClassifier._extract_loan_input() correctly parses income + loan amounts."""

    def _extract(self, msg):
        return IntentClassifier._extract_loan_input(msg)

    def test_crore_income_and_lakh_loan(self):
        msg = "My income is 1 lakh and I need a loan of 50 lakh"
        result = self._extract(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_income, 100_000)
        self.assertEqual(result.loan_amount, 5_000_000)

    def test_crore_amounts_parsed(self):
        msg = "salary hai 2 lakh, loan chahiye 1 crore, 20 year ke liye"
        result = self._extract(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result.monthly_income, 200_000)
        self.assertEqual(result.loan_amount, 10_000_000)
        self.assertEqual(result.tenure_years, 20)

    def test_apna_ghar_scheme_detected(self):
        msg = "income 80k, loan 40 lakh under apna ghar scheme"
        result = self._extract(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result.scheme, 'apna_ghar')

    def test_returns_none_when_income_missing(self):
        msg = "loan of 50 lakh for a house"
        result = self._extract(msg)
        self.assertIsNone(result)

    def test_returns_none_when_loan_amount_missing(self):
        msg = "my salary is 1.5 lakh, want a mortgage"
        result = self._extract(msg)
        self.assertIsNone(result)


class AuditInputExtractionTest(SimpleTestCase):
    """IntentClassifier._extract_audit_input() correctly parses city, value, area."""

    def _extract(self, msg):
        lower = msg.lower()
        return IntentClassifier._extract_audit_input(msg, lower, history=None)

    def test_lahore_property_with_value(self):
        msg = "Give me a detailed audit of my 5 marla house in DHA Lahore worth 2 crore"
        result = self._extract(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result.city, 'Lahore')
        self.assertEqual(result.estimated_value_pkr, 20_000_000)
        self.assertEqual(result.area_marla, 5.0)

    def test_non_pk_city_returns_none(self):
        msg = "audit my flat in Dubai worth 3 crore"
        result = self._extract(msg)
        self.assertIsNone(result)

    def test_non_pk_city_london_returns_none(self):
        msg = "property audit report for a house in London worth 5 crore"
        result = self._extract(msg)
        self.assertIsNone(result)

    def test_no_value_returns_none(self):
        msg = "give me an analysis of my house in Lahore DHA"
        result = self._extract(msg)
        self.assertIsNone(result)

    def test_location_defaults_to_city_when_no_area_keyword(self):
        msg = "I need a property audit for Lahore, value 1.5 crore"
        result = self._extract(msg)
        self.assertIsNotNone(result)
        # When no location keyword found, location should equal city
        self.assertEqual(result.location, 'Lahore')


class IntentClassifierRegressionTest(SimpleTestCase):
    """Regression: audit checked before search; greeting; general fallback."""

    def test_greeting_returns_greeting_intent(self):
        result = IntentClassifier.classify("hi")
        self.assertEqual(result.intent, 'greeting')
        self.assertEqual(result.confidence, 1.0)

    def test_audit_message_not_misrouted_as_search(self):
        msg = "Give me a property audit report for a 10 marla house in DHA Lahore worth 3 crore"
        result = IntentClassifier.classify(msg)
        self.assertEqual(result.intent, 'property_audit')

    def test_loan_keyword_without_params_gives_lower_confidence(self):
        result = IntentClassifier.classify("I want to know about home loans")
        self.assertEqual(result.intent, 'loan_eligibility')
        self.assertLess(result.confidence, 0.87)

    def test_loan_keyword_with_params_gives_high_confidence(self):
        result = IntentClassifier.classify(
            "income 1.2 lakh, loan chahiye 60 lakh, 20 saal ka plan"
        )
        self.assertEqual(result.intent, 'loan_eligibility')
        self.assertGreaterEqual(result.confidence, 0.87)

    def test_general_message_returns_general_query(self):
        result = IntentClassifier.classify("what is a good time to buy property?")
        self.assertEqual(result.intent, 'general_query')
        self.assertLess(result.confidence, 0.85)
