"""
PIIScrubber — deterministic, stateless PII scrubbing for ML dataset curation.
Patterns match those in apps.compliance.privacy_guard.PIIMaskingPipeline
plus name entity detection and property price scrubbing.
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)


class CrossTenantLeakError(Exception):
    """Raised when a message contains another org's identifier."""


_PATTERNS: list[tuple[re.Pattern, str]] = [
    # E.164 phones
    (re.compile(r'\+\d{7,15}\b'), '[PHONE]'),
    # CNIC/NIC  12345-1234567-1
    (re.compile(r'\b\d{5}-\d{7}-\d\b'), '[ID_NUMBER]'),
    # Passport  A1234567 or AB12345678
    (re.compile(r'\b[A-Z]{1,2}\d{7,9}\b'), '[PASSPORT]'),
    # IBAN  GB29NWBK60161331926819
    (re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b'), '[IBAN]'),
    # Credit/debit card (13–19 consecutive digits)
    (re.compile(r'\b(?:\d[ -]?){13,19}\b'), '[CARD_NUMBER]'),
    # Email
    (re.compile(r'\S+@\S+\.\S+'), '[EMAIL]'),
    # Property prices with currency (e.g. PKR 5,000,000  AED 1.2M  $500k)
    (re.compile(r'(?:PKR|AED|USD|GBP|SAR|EUR|INR|AUD|CAD)[\s,]?\d[\d,\.]*(?:[KkMmBb])?', re.IGNORECASE), '[AMOUNT]'),
    # Currency symbol + number
    (re.compile(r'[$£€₹]\s?\d[\d,\.]*(?:[KkMmBb])?'), '[AMOUNT]'),
    # Name entity detection: title + capitalized word sequence
    (re.compile(
        r'\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Agha|Sheikh|Syed|Ch|Raja|Rana|Malik|Mirza|Baig)\b'
        r'(?:\s+[A-Z][a-z]+){1,3}',
        re.UNICODE,
    ), '[NAME]'),
]

# Post-mask verification — these should be empty after scrubbing
_VERIFY_PATTERNS: list[re.Pattern] = [
    re.compile(r'\+\d{7,15}\b'),
    re.compile(r'\b\d{5}-\d{7}-\d\b'),
    re.compile(r'\S+@\S+\.\S+'),
]


class PIIScrubber:
    """
    Deterministic, thread-safe PII scrubber for ML dataset curation.
    Input → same output always (no randomisation).
    """

    @staticmethod
    def scrub(text: str) -> str:
        for pattern, replacement in _PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    @staticmethod
    def validate_clean(text: str) -> bool:
        """Returns False if any high-confidence PII pattern still matches."""
        return not any(p.search(text) for p in _VERIFY_PATTERNS)

    @staticmethod
    def check_cross_tenant_leak(text: str, org_id: str) -> None:
        """Raises CrossTenantLeakError if a different org's UUID appears in the text."""
        # Find any UUID-like strings in the text
        uuid_pattern = re.compile(
            r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
            re.IGNORECASE,
        )
        for match in uuid_pattern.finditer(text):
            found_id = match.group(0).lower()
            if found_id != org_id.lower():
                raise CrossTenantLeakError(
                    f"Cross-tenant UUID detected: {found_id} (expected org={org_id})"
                )
