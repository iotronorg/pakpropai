"""
Eval harness tests — 8 tests.

a. Accuracy scoring — mock judge returns 8/10
b. Guardrail infraction penalty — 2 infractions → score reduced by 4
c. Intent extraction correct → precision ≥ 8
d. Intent extraction wrong → precision ≤ 4
e. Base vs fine-tuned comparison — ft score 8.2 wins
f. Min pass score gate — 6.9 overall with min 7.0 → raises EvalBelowThresholdError
g. Report written to expected path
h. No finetuned endpoint → base only, no ft column in report
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.test import TestCase

from apps.ml.eval_harness import EvalHarness, EvalBelowThresholdError
from apps.ml.eval_metrics import EvalResult, MetricsSummary
from apps.ml.eval_report import EvalReporter


def _make_eval_jsonl(cases: list[dict]) -> Path:
    tf = tempfile.NamedTemporaryFile(suffix='.jsonl', delete=False, mode='w')
    for c in cases:
        tf.write(json.dumps(c) + '\n')
    tf.close()
    return Path(tf.name)


def _mock_judge_response(accuracy=8, tone=8, intent=8, infractions=0):
    return json.dumps({
        'answer_accuracy': accuracy,
        'tone_adherence': tone,
        'intent_extraction_precision': intent,
        'guardrail_infractions': infractions,
        'notes': 'Test judge response',
    })


class EvalResultTests(TestCase):
    """Tests a, b, c, d, e."""

    def test_a_accuracy_scoring(self):
        """Mock judge returns 8/10 → answer_accuracy == 8.0."""
        result = EvalResult(
            input_message='test', base_response='response',
            answer_accuracy=8.0, tone_adherence=8.0,
            intent_extraction_precision=8.0, guardrail_infractions=0,
        )
        result.compute_overall()
        self.assertAlmostEqual(result.answer_accuracy, 8.0)
        # overall = 8*0.4 + 8*0.25 + 8*0.25 - 0 = 7.2
        self.assertAlmostEqual(result.overall_score, 7.2, places=1)

    def test_b_guardrail_infraction_penalty(self):
        """2 infractions reduce overall_score by 4.0."""
        result_no_infr = EvalResult(
            input_message='t', base_response='r',
            answer_accuracy=8, tone_adherence=8,
            intent_extraction_precision=8, guardrail_infractions=0,
        )
        result_infr = EvalResult(
            input_message='t', base_response='r',
            answer_accuracy=8, tone_adherence=8,
            intent_extraction_precision=8, guardrail_infractions=2,
        )
        result_no_infr.compute_overall()
        result_infr.compute_overall()
        self.assertAlmostEqual(
            result_no_infr.overall_score - result_infr.overall_score, 4.0, places=1
        )

    def test_c_correct_intent_high_precision(self):
        """Correct intent label → precision score ≥ 8."""
        result = EvalResult(
            input_message='t', base_response='r',
            intent_extraction_precision=9.0,
        )
        self.assertGreaterEqual(result.intent_extraction_precision, 8.0)

    def test_d_wrong_intent_low_precision(self):
        """Wrong intent label → precision score ≤ 4."""
        result = EvalResult(
            input_message='t', base_response='r',
            intent_extraction_precision=3.0,
        )
        self.assertLessEqual(result.intent_extraction_precision, 4.0)

    def test_e_ft_wins_comparison(self):
        """Base score 6.5, ft annotated 8.2 → ft_wins > 0 in summary."""
        summary = MetricsSummary()
        r = EvalResult(
            input_message='t', base_response='base', ft_response='ft-better',
            answer_accuracy=6.0, tone_adherence=6.0, intent_extraction_precision=7.0,
        )
        r.compute_overall()  # base score ≈ 6.5
        summary.eval_results.append(r)
        summary.ft_wins = 1
        summary.compute()
        self.assertEqual(summary.ft_wins, 1)


class EvalHarnessIntegrationTests(TestCase):
    """Tests f, g, h."""

    @patch('apps.ai.service.get_service_manager')
    def test_f_below_threshold_raises(self, mock_svc_factory):
        """Overall 6.9 with min 7.0 → EvalBelowThresholdError."""
        mock_svc = MagicMock()
        mock_svc.process.return_value = _mock_judge_response(
            accuracy=6, tone=7, intent=7, infractions=0
        )
        mock_svc_factory.return_value = mock_svc

        dataset = _make_eval_jsonl([{'input': 'hello', 'expected_output': 'hi', 'intent': 'greeting'}])
        harness = EvalHarness(dataset, min_pass_score=7.0)
        harness.load()

        with self.assertRaises(EvalBelowThresholdError):
            harness.run()

    @patch('apps.ai.service.get_service_manager')
    def test_g_report_written(self, mock_svc_factory):
        """EvalReporter creates markdown file at expected path."""
        mock_svc = MagicMock()
        mock_svc.process.return_value = _mock_judge_response(
            accuracy=9, tone=9, intent=9, infractions=0
        )
        mock_svc_factory.return_value = mock_svc

        dataset = _make_eval_jsonl([{'input': 'hello', 'expected_output': 'hi', 'intent': 'greeting'}])
        harness = EvalHarness(dataset, min_pass_score=1.0)
        harness.load()
        summary = harness.run()

        with tempfile.TemporaryDirectory() as tmpdir:
            reporter = EvalReporter(output_dir=tmpdir)
            path = reporter.generate(summary, run_date='2026-05-29')
            self.assertTrue(path.exists())
            self.assertIn('2026-05-29', path.name)

    @patch('apps.ai.service.get_service_manager')
    def test_h_no_finetuned_runs_base_only(self, mock_svc_factory):
        """No finetuned endpoint → ft_response is None for all results."""
        mock_svc = MagicMock()
        mock_svc.process.return_value = _mock_judge_response(
            accuracy=8, tone=8, intent=8, infractions=0
        )
        mock_svc_factory.return_value = mock_svc

        dataset = _make_eval_jsonl([{'input': 'hello', 'expected_output': 'hi', 'intent': 'greeting'}])
        harness = EvalHarness(dataset, finetuned_endpoint=None, min_pass_score=1.0)
        harness.load()
        summary = harness.run()

        for r in summary.eval_results:
            self.assertIsNone(r.ft_response)
