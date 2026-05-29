"""
InstructionTuningBuilder — converts CandidateConversations to OpenAI/Anthropic-compatible JSONL.
All output is scrubbed with PIIScrubber; records failing validate_clean() are dropped.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from apps.ml.anonymizer import PIIScrubber, CrossTenantLeakError
from apps.ml.dataset_curator import CandidateConversation

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are RealTron AI, a professional real estate sales assistant. "
    "Help clients find properties, understand pricing, and guide them through the buying process. "
    "Be concise, helpful, and honest. Never fabricate prices or legal information."
)


@dataclass
class DatasetStats:
    total_candidates:        int = 0
    scrubbed_and_included:   int = 0
    dropped_pii_leak:        int = 0
    dropped_cross_tenant:    int = 0
    dropped_quality_below_threshold: int = 0
    dropped_empty:           int = 0


class InstructionTuningBuilder:
    """
    Converts CandidateConversation → scrubbed JSONL records.
    Each record: {"messages": [system, ...user/assistant turns]}
    """

    def build_dataset(
        self,
        candidates: list[CandidateConversation],
        output_path: Path,
        min_quality: float = 0.7,
    ) -> DatasetStats:
        stats = DatasetStats(total_candidates=len(candidates))
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open('w', encoding='utf-8') as fout:
            for candidate in candidates:
                record, drop_reason = self._process_candidate(candidate, min_quality)

                if drop_reason == 'pii_leak':
                    stats.dropped_pii_leak += 1
                elif drop_reason == 'cross_tenant':
                    stats.dropped_cross_tenant += 1
                elif drop_reason == 'quality':
                    stats.dropped_quality_below_threshold += 1
                elif drop_reason == 'empty':
                    stats.dropped_empty += 1
                elif record is not None:
                    fout.write(json.dumps(record, ensure_ascii=False) + '\n')
                    stats.scrubbed_and_included += 1

        return stats

    def _process_candidate(
        self,
        candidate: CandidateConversation,
        min_quality: float,
    ) -> tuple[dict | None, str | None]:
        """
        Returns (record, None) on success or (None, drop_reason) on failure.
        Always returns a 2-tuple.
        """
        if candidate.quality_score < min_quality:
            return None, 'quality'

        if not candidate.messages:
            return None, 'empty'

        scrubber = PIIScrubber()
        scrubbed_turns = []

        for turn in candidate.messages:
            try:
                scrubbed = scrubber.scrub(turn['content'])

                # Cross-tenant check
                if candidate.org_id:
                    try:
                        scrubber.check_cross_tenant_leak(scrubbed, candidate.org_id)
                    except CrossTenantLeakError:
                        logger.debug('Dropped cross-tenant record session=%s', candidate.session_id)
                        return None, 'cross_tenant'

                # Hard gate
                if not scrubber.validate_clean(scrubbed):
                    logger.debug('Dropped PII-leak record session=%s', candidate.session_id)
                    return None, 'pii_leak'

                scrubbed_turns.append({'role': turn['role'], 'content': scrubbed})

            except CrossTenantLeakError:
                return None, 'cross_tenant'
            except Exception:
                logger.warning('PIIScrubber failed session=%s', candidate.session_id, exc_info=True)
                return None, 'pii_leak'

        if not scrubbed_turns:
            return None, 'empty'

        record = {
            'messages': [
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                *scrubbed_turns,
            ]
        }
        return record, None
