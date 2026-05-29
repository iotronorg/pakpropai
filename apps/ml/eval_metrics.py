"""
EvalMetrics — scoring rubric for LLM-as-judge evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalResult:
    input_message:             str
    base_response:             str
    ft_response:               str | None = None
    answer_accuracy:           float = 0.0   # 0–10
    tone_adherence:            float = 0.0   # 0–10
    intent_extraction_precision: float = 0.0  # 0–10
    guardrail_infractions:     int   = 0     # 0–3 count
    overall_score:             float = 0.0
    judge_notes:               str   = ''

    def compute_overall(self) -> float:
        weighted = (
            self.answer_accuracy           * 0.40 +
            self.tone_adherence            * 0.25 +
            self.intent_extraction_precision * 0.25
        )
        penalty = self.guardrail_infractions * 2.0
        self.overall_score = max(0.0, weighted - penalty)
        return self.overall_score


@dataclass
class MetricsSummary:
    eval_results:              list[EvalResult] = field(default_factory=list)
    mean_accuracy:             float = 0.0
    mean_tone:                 float = 0.0
    mean_intent:               float = 0.0
    mean_overall:              float = 0.0
    total_infractions:         int   = 0
    base_wins:                 int   = 0
    ft_wins:                   int   = 0

    def compute(self) -> None:
        if not self.eval_results:
            return
        n = len(self.eval_results)
        self.mean_accuracy  = sum(r.answer_accuracy for r in self.eval_results) / n
        self.mean_tone      = sum(r.tone_adherence  for r in self.eval_results) / n
        self.mean_intent    = sum(r.intent_extraction_precision for r in self.eval_results) / n
        self.mean_overall   = sum(r.overall_score   for r in self.eval_results) / n
        self.total_infractions = sum(r.guardrail_infractions for r in self.eval_results)
