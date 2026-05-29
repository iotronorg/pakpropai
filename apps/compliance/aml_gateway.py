from __future__ import annotations
import hashlib
import logging
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

FUZZY_FLAG_THRESHOLD = 0.85


@dataclass
class ScreeningResult:
    status: str          # clear | flagged | blocked
    risk_score: int
    matched_records: list = field(default_factory=list)


class AMLScreeningGateway:

    @classmethod
    def screen_entity(cls, name: str, id_number: str, id_type: str, org) -> ScreeningResult:
        """
        Screen name + id_number against ComplianceSanctionRecord.
        Fail-open: any exception → returns clear + logs WARNING.
        """
        try:
            return cls._screen(name, id_number, id_type, org)
        except Exception as exc:
            logger.warning('AMLScreeningGateway fail-open: %s', exc)
            return ScreeningResult(status='clear', risk_score=0)

    @classmethod
    def _screen(cls, name: str, id_number: str, id_type: str, org) -> ScreeningResult:
        from django.db.models import Q
        from .models import ComplianceSanctionRecord, SanctionScreeningResult

        norm_name = cls._normalize_name(name)
        id_prefix = (id_number or '')[:4]
        id_hash   = cls._hash_id(id_number) if id_number else ''

        # Platform-level (org=null) match all orgs; org-level match own org only
        records = ComplianceSanctionRecord.objects.filter(
            Q(org__isnull=True) | Q(org=org),
            is_active=True,
        )

        best_record     = None
        best_match_type = None
        best_score      = 0

        for rec in records:
            # ID hash match — highest priority, immediate blocked
            if id_hash and rec.id_number_hash and id_hash == rec.id_number_hash:
                best_record     = rec
                best_match_type = 'id_match'
                best_score      = 100
                break

            ratio = SequenceMatcher(None, norm_name, cls._normalize_name(rec.name)).ratio()
            if ratio >= 1.0:
                score = 100
                match_type = 'exact'
            elif ratio >= FUZZY_FLAG_THRESHOLD:
                score = int(ratio * 100)
                match_type = 'fuzzy'
            else:
                continue

            if score > best_score:
                best_record     = rec
                best_match_type = match_type
                best_score      = score

        if best_record is None:
            return ScreeningResult(status='clear', risk_score=0)

        final_status = 'blocked' if best_match_type in ('exact', 'id_match') else 'flagged'

        try:
            SanctionScreeningResult.objects.create(
                screened_name=name,
                id_number_prefix=id_prefix,
                id_number_hash=id_hash,
                list_source=best_record.list_source,
                match_type=best_match_type,
                risk_score=best_score,
                org=org,
                status=final_status,
            )
        except Exception as exc:
            logger.warning('AMLScreeningGateway: failed to persist result: %s', exc)

        return ScreeningResult(
            status=final_status,
            risk_score=best_score,
            matched_records=[best_record],
        )

    @staticmethod
    def _normalize_name(name: str) -> str:
        nfkd = unicodedata.normalize('NFKD', name or '')
        stripped = ''.join(c for c in nfkd if not unicodedata.combining(c))
        return ' '.join(stripped.lower().split())

    @staticmethod
    def _hash_id(id_number: str) -> str:
        return hashlib.sha256((id_number or '').strip().encode()).hexdigest()
