from unittest.mock import patch

from django.test import TestCase

from apps.verification.services import DocumentComplianceEngine, OCRPipelineService


# ── OCRPipelineService ────────────────────────────────────────────────────────

class OCRPipelineStubTest(TestCase):

    def test_stub_returns_required_keys(self):
        result = OCRPipelineService._stub(b'any_bytes')
        for key in ('raw_text', 'confidence', 'backend', 'error'):
            self.assertIn(key, result)

    def test_stub_backend_label(self):
        self.assertEqual(OCRPipelineService._stub(b'')['backend'], 'stub')

    def test_stub_high_confidence(self):
        self.assertEqual(OCRPipelineService._stub(b'data')['confidence'], 'HIGH')

    def test_stub_no_error(self):
        self.assertIsNone(OCRPipelineService._stub(b'data')['error'])

    def test_stub_raw_text_not_empty(self):
        result = OCRPipelineService._stub(b'data')
        self.assertTrue(len(result['raw_text']) > 0)


class OCRPipelineExtractTest(TestCase):

    @patch('apps.verification.services.OCRPipelineService._configured_backend',
           return_value='stub')
    def test_extract_delegates_to_stub(self, _):
        result = OCRPipelineService.extract(b'test_bytes')
        self.assertEqual(result['backend'], 'stub')
        self.assertIn('raw_text', result)

    def test_tesseract_graceful_missing_dependency(self):
        with patch.dict('sys.modules', {'pytesseract': None, 'PIL': None}):
            result = OCRPipelineService._tesseract(b'data')
        self.assertEqual(result['backend'], 'tesseract')
        self.assertIsNotNone(result['error'])
        self.assertEqual(result['raw_text'], '')

    def test_google_vision_graceful_missing_dependency(self):
        with patch.dict('sys.modules', {'google.cloud': None, 'google.cloud.vision': None}):
            result = OCRPipelineService._google_vision(b'data')
        self.assertEqual(result['backend'], 'google_vision')
        self.assertIsNotNone(result['error'])

    def test_document_ai_missing_config_returns_error(self):
        with patch('apps.verification.services.OCRPipelineService._configured_backend',
                   return_value='document_ai'):
            with patch.dict('sys.modules', {'google.cloud.documentai': None}):
                result = OCRPipelineService._document_ai(b'data')
        self.assertIsNotNone(result.get('error'))


# ── DocumentComplianceEngine ──────────────────────────────────────────────────

class DocumentComplianceEngineTest(TestCase):

    _CLEAN_OCR = (
        'OWNER: Ahmed Khan\nCNIC: 35202-1234567-8\n'
        'ADDRESS: Plot 5, DHA Phase 6, Lahore\nAREA: 10 Marla\n'
        'REG_NUMBER: LHR-2024-00123\nDATE: 15-01-2024\n'
        'AUTHORITY: PLRA Lahore\nFLAGS: NONE\nCONFIDENCE: HIGH\n'
    )

    def test_clean_doc_low_risk(self):
        result = DocumentComplianceEngine.analyze(self._CLEAN_OCR)
        self.assertEqual(result['scam_risk_level'], 'low')
        self.assertEqual(result['flags'], [])

    def test_clean_doc_trusted_authority_detected(self):
        result = DocumentComplianceEngine.analyze(self._CLEAN_OCR)
        self.assertTrue(result['trusted_authority_mentioned'])

    def test_high_risk_keywords_raise_score(self):
        suspicious = (
            'seller said no documents available, '
            'advance payment required, already sold to another buyer'
        )
        result = DocumentComplianceEngine.analyze(suspicious)
        self.assertGreaterEqual(result['scam_risk_score'], 50)
        self.assertEqual(result['scam_risk_level'], 'high')
        self.assertTrue(result['flags'])

    def test_authority_reduces_risk_score(self):
        # Authority mention (plra) should reduce final score by 15
        text = 'urgent sale plra registered property, advance payment needed'
        result = DocumentComplianceEngine.analyze(text)
        raw = sum(
            pts for kw, _, pts in DocumentComplianceEngine._load_rules()
            if kw in text.lower()
        )
        expected = DocumentComplianceEngine.scam_risk_score(raw, authority_found=True)
        self.assertEqual(result['scam_risk_score'], expected)

    def test_scam_risk_score_capped_at_100(self):
        self.assertEqual(DocumentComplianceEngine.scam_risk_score(9999), 100)

    def test_scam_risk_score_zero_base(self):
        self.assertEqual(DocumentComplianceEngine.scam_risk_score(0), 0)

    def test_authority_does_not_reduce_zero_score(self):
        self.assertEqual(DocumentComplianceEngine.scam_risk_score(0, authority_found=True), 0)

    def test_investment_signal_high_risk(self):
        sig = DocumentComplianceEngine._investment_signal(
            scam_score=75, authority_found=False,
            fields={'owner_name': True, 'cnic': True},
        )
        self.assertEqual(sig, 'HIGH_RISK')

    def test_investment_signal_clear(self):
        sig = DocumentComplianceEngine._investment_signal(
            scam_score=0, authority_found=True,
            fields={'owner_name': True, 'cnic': True, 'area': True, 'authority': True},
        )
        self.assertEqual(sig, 'CLEAR')

    def test_investment_signal_needs_review_partial_fields(self):
        sig = DocumentComplianceEngine._investment_signal(
            scam_score=10, authority_found=False,
            fields={'owner_name': True, 'cnic': False, 'area': False, 'authority': False},
        )
        self.assertEqual(sig, 'NEEDS_REVIEW')

    def test_check_required_fields_detects_marla(self):
        fields = DocumentComplianceEngine._check_required_fields('area 5 marla plra')
        self.assertTrue(fields['area'])
        self.assertTrue(fields['authority'])

    def test_check_required_fields_detects_owner(self):
        fields = DocumentComplianceEngine._check_required_fields('owner: Muhammad Ali')
        self.assertTrue(fields['owner_name'])

    def test_check_required_fields_all_missing(self):
        fields = DocumentComplianceEngine._check_required_fields('random text here xyz')
        self.assertFalse(fields['owner_name'])
        self.assertFalse(fields['area'])
        self.assertFalse(fields['authority'])

    def test_analyze_returns_all_required_keys(self):
        result = DocumentComplianceEngine.analyze('some document text')
        for key in ('flags', 'scam_risk_score', 'scam_risk_level',
                    'trusted_authority_mentioned', 'required_fields_present',
                    'investment_signal'):
            self.assertIn(key, result)
