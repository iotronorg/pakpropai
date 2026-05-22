import logging
from django.core.cache import cache
from django.utils import timezone

from apps.ai.scoring import fraud_check as _ai_fraud_check

logger = logging.getLogger(__name__)


class VerificationSignalService:
    """Computes a 0-100 integrity score from all available signals on a Verification."""

    # Document types that carry the most legal weight in Pakistan
    CORE_DOC_TYPES = {'fard', 'sale_deed', 'allotment'}

    @classmethod
    def compute_score(cls, verification) -> int:
        scans = list(verification.document_scans.all())
        score = 40  # neutral baseline

        # --- document signals ---
        for scan in scans:
            if scan.confidence == 'HIGH':
                score += 12
            elif scan.confidence == 'MEDIUM':
                score += 6
            else:
                score += 2

            # deduct for each red flag found in this scan
            score -= len(scan.red_flags) * 15

        # bonus: has at least one core legal document
        submitted_types = {s.document_type for s in scans}
        if submitted_types & cls.CORE_DOC_TYPES:
            score += 15

        # bonus: multiple document types (completeness)
        if len(submitted_types) >= 3:
            score += 10

        # --- verification-level fraud flags ---
        score -= len(verification.fraud_flags) * 20

        # bonus: no flags anywhere
        all_red_flags = sum(len(s.red_flags) for s in scans)
        if all_red_flags == 0 and not verification.fraud_flags and scans:
            score += 10

        return max(0, min(100, score))

    @classmethod
    def refresh(cls, verification) -> int:
        score = cls.compute_score(verification)
        verification.signal_score = score
        verification.save(update_fields=['signal_score'])
        return score


class FraudCheckService:

    BLACKLIST_KEY = 'fraud:blacklist:'

    @classmethod
    def check(cls, query: str, user=None) -> dict:
        # 1. Quick blacklist lookup (fast path, no AI cost)
        for token in query.lower().split():
            if cache.get(f"{cls.BLACKLIST_KEY}{token}"):
                return {
                    'risk': 'high',
                    'flags': [f'"{token}" is on the known fraud blacklist.'],
                    'recommendation': 'Avoid this transaction immediately.',
                    'verify_steps': ['Report to FBR Property Verification Portal',
                                     'File complaint with local police'],
                    'source': 'blacklist',
                }

        # 2. AI-powered analysis
        try:
            ai_result = _ai_fraud_check(query)
        except Exception as exc:
            logger.error(f"Fraud AI failed: {exc}")
            return {
                'risk': 'unknown',
                'flags': ['AI service temporarily unavailable.'],
                'recommendation': 'Try again in a few minutes.',
                'verify_steps': [],
                'source': 'fallback',
            }

        ai_result['source'] = 'ai'
        return ai_result

    @classmethod
    def add_to_blacklist(cls, token: str, ttl_days: int = 7):
        cache.set(f"{cls.BLACKLIST_KEY}{token.lower()}", True, ttl_days * 86400)


# ── OCR Pipeline ───────────────────────────────────────────────────────────────

class OCRPipelineService:
    """
    Pluggable OCR backend. Swap the active backend via SystemConfig key
    `ocr_backend` (stub | tesseract | google_vision | document_ai) without
    changing any call sites.

    All public methods return a uniform dict:
        { raw_text, confidence (HIGH|MEDIUM|LOW), backend, error }
    """

    @classmethod
    def extract(cls, file_bytes: bytes, mime_type: str = 'image/jpeg') -> dict:
        """Entry point: extract raw text from a document image or PDF bytes."""
        backend = cls._configured_backend()
        handlers = {
            'tesseract':    cls._tesseract,
            'google_vision': cls._google_vision,
            'document_ai':  cls._document_ai,
        }
        handler = handlers.get(backend, cls._stub)
        try:
            return handler(file_bytes, mime_type)
        except Exception as exc:
            logger.error(f"OCR extraction failed (backend={backend}): {exc}")
            return {'raw_text': '', 'confidence': 'LOW', 'backend': backend, 'error': str(exc)}

    @classmethod
    def _configured_backend(cls) -> str:
        try:
            from apps.config.services import SystemConfigService
            return SystemConfigService.get('ocr_backend', 'stub')
        except Exception:
            return 'stub'

    @classmethod
    def _stub(cls, file_bytes: bytes, mime_type: str = '') -> dict:
        """Deterministic no-op — returns recognizable placeholder for local/test use."""
        return {
            'raw_text': (
                'OWNER: Test Owner\nCNIC: 35202-1234567-8\n'
                'ADDRESS: Plot 5, DHA Phase 6, Lahore\nAREA: 10 Marla\n'
                'REG_NUMBER: LHR-2024-00123\nDATE: 15-01-2024\n'
                'AUTHORITY: PLRA Lahore\nFLAGS: NONE\nCONFIDENCE: HIGH\n'
                'NOTES: Stub — set ocr_backend in SystemConfig for real extraction.'
            ),
            'confidence': 'HIGH',
            'backend': 'stub',
            'error': None,
        }

    @classmethod
    def _tesseract(cls, file_bytes: bytes, mime_type: str = '') -> dict:
        """Tesseract OCR. Requires: pip install pytesseract Pillow"""
        try:
            import pytesseract
            from PIL import Image
            import io as _io
            img = Image.open(_io.BytesIO(file_bytes))
            text = pytesseract.image_to_string(img, lang='eng+urd')
            length = len(text.strip())
            confidence = 'HIGH' if length > 200 else ('MEDIUM' if length > 50 else 'LOW')
            return {'raw_text': text, 'confidence': confidence, 'backend': 'tesseract', 'error': None}
        except ImportError:
            logger.error('pytesseract not installed — pip install pytesseract Pillow')
            return {'raw_text': '', 'confidence': 'LOW', 'backend': 'tesseract',
                    'error': 'pytesseract not installed'}

    @classmethod
    def _google_vision(cls, file_bytes: bytes, mime_type: str = '') -> dict:
        """Google Cloud Vision OCR. Requires: pip install google-cloud-vision"""
        try:
            from google.cloud import vision as _vision
            client = _vision.ImageAnnotatorClient()
            image = _vision.Image(content=file_bytes)
            response = client.text_detection(image=image)
            annotations = response.text_annotations
            raw = annotations[0].description if annotations else ''
            confidence = 'HIGH' if len(raw.strip()) > 200 else ('MEDIUM' if raw.strip() else 'LOW')
            return {'raw_text': raw, 'confidence': confidence, 'backend': 'google_vision', 'error': None}
        except ImportError:
            logger.error('google-cloud-vision not installed — pip install google-cloud-vision')
            return {'raw_text': '', 'confidence': 'LOW', 'backend': 'google_vision',
                    'error': 'google-cloud-vision not installed'}

    @classmethod
    def _document_ai(cls, file_bytes: bytes, mime_type: str = 'image/jpeg') -> dict:
        """Google Document AI. Requires: pip install google-cloud-documentai"""
        try:
            from google.cloud import documentai as _docai
            from apps.config.services import SystemConfigService
            project_id   = SystemConfigService.get('google_docai_project_id', '')
            processor_id = SystemConfigService.get('google_docai_processor_id', '')
            location     = SystemConfigService.get('google_docai_location', 'us')
            if not (project_id and processor_id):
                return {
                    'raw_text': '', 'confidence': 'LOW', 'backend': 'document_ai',
                    'error': 'google_docai_project_id / google_docai_processor_id not configured',
                }
            client = _docai.DocumentProcessorServiceClient()
            name = client.processor_path(project_id, location, processor_id)
            raw_doc = _docai.RawDocument(content=file_bytes, mime_type=mime_type)
            result = client.process_document(
                request=_docai.ProcessRequest(name=name, raw_document=raw_doc)
            )
            text = result.document.text
            confidence = 'HIGH' if len(text.strip()) > 200 else ('MEDIUM' if text.strip() else 'LOW')
            return {'raw_text': text, 'confidence': confidence, 'backend': 'document_ai', 'error': None}
        except ImportError:
            logger.error('google-cloud-documentai not installed — pip install google-cloud-documentai')
            return {'raw_text': '', 'confidence': 'LOW', 'backend': 'document_ai',
                    'error': 'google-cloud-documentai not installed'}


# ── Document Compliance Engine ─────────────────────────────────────────────────

# Default compliance rule set.
# Each tuple: (keyword_to_match, human_readable_flag, risk_points).
# Org-admins can override per-org via SystemConfig key `doc_compliance_flags_<org_id>` (JSON).
_DEFAULT_COMPLIANCE_FLAGS: list[tuple[str, str, int]] = [
    ('advance payment',      'Advance payment demanded before documents shown',         45),
    ('token first',          'Token demanded before any documents — major red flag',     35),
    ('overseas',             'Overseas seller — verify in-person presence first',        25),
    ('urgent sale',          'Urgency pressure tactic',                                  15),
    ('kachhi file',          'Unallocated (kachhi) file — verify with authority',        55),
    ('kachi file',           'Unallocated (kachi) file — verify with authority',         55),
    ('kachha file',          'Unallocated (kachha) file — verify with authority',        55),
    ('file not allotted',    'File not yet allotted — speculative investment',           50),
    ('power of attorney',    'PoA involved — validate at sub-registrar',                 25),
    ('court case',           'Litigation mentioned — do not proceed until resolved',     65),
    ('court mein',           'Property in court proceedings — do not proceed',           65),
    ('no fard',              'Seller cannot provide Fard ownership record',              55),
    ('fard nahi',            'Seller cannot provide Fard (Urdu indicator)',              55),
    ('no documents',         'Seller has no supporting documents — extremely high risk', 70),
    ('documents nahi',       'No documents available (Urdu indicator)',                  70),
    ('below market',         'Price significantly below market — investigate reason',    25),
    ('double sale',          'Potential double-sale scenario',                           60),
    ('already sold',         'Already-sold claim — possible double sale',                60),
    ('double bech',          'Possible double-sale (Urdu indicator)',                    60),
    ('no noc',               'No NOC from authority — transfer likely blocked',          40),
    ('noc nahi',             'No NOC (Urdu indicator)',                                  40),
    ('society not approved', 'Non-approved housing society',                             50),
    ('fake registry',        'Forged/fake registry document',                            70),
    ('guaranteed return',    'Guaranteed return promise — not possible in property',     35),
    ('pehle paise',          'Advance payment demanded (Urdu indicator)',                45),
    ('pehle token',          'Token before documents (Urdu indicator)',                  35),
    ('jaldi karo',           'Urgency pressure — rush tactic (Urdu)',                    20),
    ('poa hai',              'PoA present — validate at sub-registrar (Urdu)',           25),
]

_TRUSTED_AUTHORITY_TOKENS = frozenset({
    'plra', 'lda', 'cda', 'rda', 'dha', 'bahria', 'nphda', 'fgehf',
    'kda', 'sbca', 'mda', 'sub-registrar', 'sub registrar',
    'fbr', 'nha', 'pca', 'kcr',
})


class DocumentComplianceEngine:
    """
    Evaluates raw OCR-extracted text against a customizable compliance rule set.
    Returns a structured analysis dict including a 0–100 Scam Risk Score and
    a high-level investment signal.

    Org-specific rules are loaded when organization_id is provided;
    falls back to _DEFAULT_COMPLIANCE_FLAGS.
    """

    @classmethod
    def analyze(
        cls, raw_text: str, doc_type: str = 'other', organization_id: str = '',
    ) -> dict:
        """
        Evaluate OCR text against compliance rules.
        Returns flags, risk score, and an investment signal.
        """
        text_lower = raw_text.lower()
        rules = cls._load_rules(organization_id)

        flags: list[str] = []
        raw_points = 0
        for keyword, message, pts in rules:
            if keyword in text_lower and message not in flags:
                flags.append(message)
                raw_points += pts

        authority_found = any(tok in text_lower for tok in _TRUSTED_AUTHORITY_TOKENS)
        score = cls.scam_risk_score(raw_points, authority_found)
        field_check = cls._check_required_fields(raw_text)

        return {
            'flags': flags,
            'scam_risk_score': score,
            'scam_risk_level': 'high' if score >= 50 else ('medium' if score >= 25 else 'low'),
            'trusted_authority_mentioned': authority_found,
            'required_fields_present': field_check,
            'investment_signal': cls._investment_signal(score, authority_found, field_check),
        }

    @classmethod
    def scam_risk_score(cls, raw_points: int, authority_found: bool = False) -> int:
        """
        Converts raw accumulated risk points to a 0–100 score.
        A trusted authority token in the document reduces the final score by 15.
        """
        score = min(raw_points, 100)
        if authority_found and score > 0:
            score = max(0, score - 15)
        return score

    @classmethod
    def _load_rules(cls, organization_id: str = '') -> list[tuple[str, str, int]]:
        """Load compliance rules; org-level JSON override via SystemConfig if present."""
        if not organization_id:
            return _DEFAULT_COMPLIANCE_FLAGS
        try:
            from apps.config.services import SystemConfigService
            import json
            raw = SystemConfigService.get(f'doc_compliance_flags_{organization_id}', '')
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return _DEFAULT_COMPLIANCE_FLAGS

    @classmethod
    def _check_required_fields(cls, raw_text: str) -> dict[str, bool]:
        """Detect whether critical document fields appear in the extracted text."""
        t = raw_text.lower()
        has_national_id = (
            '-' in raw_text
            and any(c.isdigit() for c in raw_text)
            and len([c for c in raw_text if c.isdigit()]) >= 13
        )
        return {
            'owner_name': any(kw in t for kw in ('owner', 'allottee', 'buyer', 'seller', 'malik')),
            'national_id': has_national_id,
            'area':        any(kw in t for kw in ('marla', 'kanal', 'sqft', 'sqm', 'acre', 'ruqba')),
            'authority':   any(tok in t for tok in _TRUSTED_AUTHORITY_TOKENS),
        }

    @staticmethod
    def _investment_signal(scam_score: int, authority_found: bool, fields: dict) -> str:
        """Derive a high-level investment signal from the compliance analysis."""
        if scam_score >= 50:
            return 'HIGH_RISK'
        completeness = sum(fields.values()) / max(len(fields), 1)
        if scam_score == 0 and authority_found and completeness >= 0.75:
            return 'CLEAR'
        if scam_score < 25 and completeness >= 0.5:
            return 'LOW_RISK'
        return 'NEEDS_REVIEW'