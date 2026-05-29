"""
EvalReporter — generates a markdown eval report + eval_results.jsonl.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from apps.ml.eval_metrics import EvalResult, MetricsSummary

logger = logging.getLogger(__name__)


class EvalReporter:
    """Generates markdown report and JSONL results file from a MetricsSummary."""

    def __init__(self, output_dir: Path | str = 'docs/ml/eval-reports'):
        self.output_dir = Path(output_dir)

    def generate(self, summary: MetricsSummary, run_date: str | None = None) -> Path:
        """
        Writes markdown report to docs/ml/eval-reports/YYYY-MM-DD-eval.md
        and eval_results.jsonl in the same directory.
        Returns the markdown path.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        today = run_date or date.today().isoformat()
        md_path   = self.output_dir / f'{today}-eval.md'
        jsonl_path = self.output_dir / 'eval_results.jsonl'

        # Write JSONL
        with jsonl_path.open('w', encoding='utf-8') as f:
            for r in summary.eval_results:
                f.write(json.dumps({
                    'input':                    r.input_message,
                    'base_response':            r.base_response,
                    'ft_response':              r.ft_response,
                    'answer_accuracy':          r.answer_accuracy,
                    'tone_adherence':           r.tone_adherence,
                    'intent_extraction_precision': r.intent_extraction_precision,
                    'guardrail_infractions':    r.guardrail_infractions,
                    'overall_score':            r.overall_score,
                    'judge_notes':              r.judge_notes,
                }, ensure_ascii=False) + '\n')

        # Write markdown
        has_ft = any(r.ft_response for r in summary.eval_results)
        lines = [
            f'# Eval Report — {today}',
            '',
            '## Summary',
            '',
            f'| Metric | Score |',
            f'|--------|-------|',
            f'| Mean Overall | {summary.mean_overall:.2f} |',
            f'| Mean Accuracy | {summary.mean_accuracy:.2f} |',
            f'| Mean Tone | {summary.mean_tone:.2f} |',
            f'| Mean Intent | {summary.mean_intent:.2f} |',
            f'| Total Infractions | {summary.total_infractions} |',
            f'| Cases | {len(summary.eval_results)} |',
            '',
        ]

        if has_ft:
            lines += [
                '## Win/Loss (Base vs Fine-Tuned)',
                '',
                f'| | Count |',
                f'|--|-------|',
                f'| Base wins | {summary.base_wins} |',
                f'| FT wins | {summary.ft_wins} |',
                '',
            ]

        # Per-case table
        lines += [
            '## Per-Case Scores',
            '',
            '| # | Input (truncated) | Acc | Tone | Intent | Infractions | Overall |',
            '|---|------------------|-----|------|--------|-------------|---------|',
        ]
        for i, r in enumerate(summary.eval_results, 1):
            trunc = r.input_message[:40].replace('|', '\\|')
            lines.append(
                f'| {i} | {trunc} | {r.answer_accuracy:.1f} | {r.tone_adherence:.1f} '
                f'| {r.intent_extraction_precision:.1f} | {r.guardrail_infractions} | {r.overall_score:.2f} |'
            )
        lines.append('')

        # Worst cases
        worst = sorted(summary.eval_results, key=lambda r: r.answer_accuracy)[:3]
        if worst:
            lines += ['## Worst-Performing Cases (by accuracy)', '']
            for r in worst:
                lines += [
                    f'**Input:** {r.input_message[:80]}',
                    f'**Score:** accuracy={r.answer_accuracy:.1f} overall={r.overall_score:.2f}',
                    f'**Notes:** {r.judge_notes}',
                    '',
                ]

        md_path.write_text('\n'.join(lines), encoding='utf-8')
        logger.info('EvalReporter: wrote %s', md_path)
        return md_path
