import logging
import re
from dataclasses import dataclass, field

from django.core.cache import cache

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    pattern_name: str
    masked_value: str
    start: int
    end: int


@dataclass
class MaskedResult:
    masked_text: str
    detections: list[Detection] = field(default_factory=list)


_PATTERNS: list[tuple[str, str, str]] = [
    # (name, regex, replacement)
    ("phone",    r"\+\d{7,15}",                                             "[PHONE]"),
    ("cnic",     r"\d{5}-\d{7}-\d",                                         "[ID_NUMBER]"),
    ("passport", r"\b[A-Z]{1,2}\d{7,9}\b",                                  "[PASSPORT]"),
    ("iban",     r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b",                        "[IBAN]"),
    ("card",     r"\b(?:\d[ -]?){13,19}\b",                                  "[CARD_NUMBER]"),
    ("account",  r"(?i)(?:account|acc)[^\d]{0,10}(\d{8,18})",               "[ACCOUNT]"),
    ("email",    r"\S+@\S+\.\S+",                                            "[EMAIL]"),
]

_COMPILED: list[tuple[str, re.Pattern, str]] = [
    (name, re.compile(pattern), replacement)
    for name, pattern, replacement in _PATTERNS
]


class PIIMaskingPipeline:
    """Stateless, thread-safe PII masking pipeline."""

    def mask(self, text: str) -> MaskedResult:
        detections: list[Detection] = []
        result = text
        offset = 0

        for name, pattern, replacement in _COMPILED:
            new_result = []
            last = 0
            for m in pattern.finditer(result):
                new_result.append(result[last:m.start()])
                new_result.append(replacement)
                detections.append(Detection(
                    pattern_name=name,
                    masked_value=replacement,
                    start=m.start() + offset,
                    end=m.end() + offset,
                ))
                last = m.end()
            new_result.append(result[last:])
            masked = "".join(new_result)
            offset += len(masked) - len(result)
            result = masked

        return MaskedResult(masked_text=result, detections=detections)

    def validate_clean(self, text: str) -> bool:
        """Returns False if any PII pattern still matches — hard gate for callers."""
        for _, pattern, _ in _COMPILED:
            if pattern.search(text):
                return False
        return True


_COUNTRY_REGULATIONS: dict[str, list[str]] = {
    # EU member states → GDPR (sample; full list via org.data_residency_region)
    'DE': ['GDPR'], 'FR': ['GDPR'], 'IT': ['GDPR'], 'ES': ['GDPR'],
    'NL': ['GDPR'], 'BE': ['GDPR'], 'PL': ['GDPR'], 'SE': ['GDPR'],
    'AT': ['GDPR'], 'DK': ['GDPR'], 'FI': ['GDPR'], 'IE': ['GDPR'],
    'PT': ['GDPR'], 'GR': ['GDPR'], 'CZ': ['GDPR'], 'HU': ['GDPR'],
    'RO': ['GDPR'], 'SK': ['GDPR'], 'BG': ['GDPR'], 'HR': ['GDPR'],
    'LT': ['GDPR'], 'LV': ['GDPR'], 'EE': ['GDPR'], 'SI': ['GDPR'],
    'CY': ['GDPR'], 'LU': ['GDPR'], 'MT': ['GDPR'],
    'GB': ['UK_GDPR'],
    'US': ['CCPA'],
    'PK': ['PDPA_PK'],
    'AE': ['PDPL_UAE'],
}
_JURISDICTION_CACHE_TTL = 3600


class JurisdictionComplianceGate:
    """Determines active data-protection regulations for an org."""

    def is_gdpr_org(self, org) -> bool:
        from apps.compliance.utils import org_is_gdpr_jurisdiction
        return org_is_gdpr_jurisdiction(org)

    def is_ccpa_org(self, org) -> bool:
        try:
            return getattr(org, 'country', '') == 'US'
        except Exception:
            return False

    def get_active_regulations(self, org) -> list[str]:
        try:
            cache_key = f'jurisdiction:{org.id}'
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
            country = getattr(org, 'country', '') or ''
            regulations = _COUNTRY_REGULATIONS.get(country.upper(), ['LOCAL'])
            cache.set(cache_key, regulations, _JURISDICTION_CACHE_TTL)
            return regulations
        except Exception:
            logger.warning("JurisdictionComplianceGate.get_active_regulations failed", exc_info=True)
            return ['LOCAL']

    def check_data_transfer_allowed(self, org, destination_region: str) -> bool:
        try:
            from apps.compliance.regional_router import RegionalDataRouter
            decision = RegionalDataRouter().validate_transfer(org, destination_region)
            return decision.allowed
        except Exception:
            logger.warning("JurisdictionComplianceGate.check_data_transfer_allowed failed", exc_info=True)
            return True
